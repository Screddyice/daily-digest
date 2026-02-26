#!/usr/bin/env python3
"""
Daily Digest for The Ready Consult
End-of-day post-call briefing (7 PM PST): what happened today, open items, and what's coming tomorrow.
Sends via email and Slack DM.
"""

import os
import json
import urllib.request
import urllib.parse
import base64
import ssl
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from email.mime.text import MIMEText

# Configuration
MATON_BASE = "https://gateway.maton.ai"
LINEAR_API = "https://gateway.maton.ai/linear/graphql"
SHAWN_EMAIL = "shawn@thereadyconsult.com"
CC_EMAIL = "Jamil@thereadyconsult.com"
SLACK_CHANNEL = "D0AH3TF0YAD"
MEETING_NOTES_FOLDER = "1kQ2qRpeYcr9ZFboPGM3sz_4vQBNvVZu8"

MATON_API_KEY = os.environ.get("MATON_API_KEY", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

PST = timezone(timedelta(hours=-8))
EST = timezone(timedelta(hours=-5))


class DailyDigest:

    def __init__(self):
        self.now = datetime.now(PST)
        self.today_str = self.now.strftime("%B %d, %Y")

    # ==================== API HELPERS ====================

    def maton_request(self, endpoint: str, method: str = "GET", data: Optional[Dict] = None, extra_headers: Optional[Dict] = None) -> Any:
        url = f"{MATON_BASE}/{endpoint}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {MATON_API_KEY}")
        if data:
            req.add_header("Content-Type", "application/json")
        if extra_headers:
            for k, v in extra_headers.items():
                req.add_header(k, v)
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, context=ctx, timeout=30)
            return json.load(resp)
        except Exception as e:
            print(f"Maton error ({endpoint}): {e}")
            return None

    def api_request(self, url: str, headers: Dict, data: Optional[Dict] = None) -> Any:
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method="POST" if data else "GET")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, context=ctx, timeout=30)
            return json.load(resp)
        except Exception as e:
            print(f"API error ({url}): {e}")
            return None

    def claude_request(self, system: str, user: str, max_tokens: int = 4000) -> str:
        data = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}]
        }
        body = json.dumps(data).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST")
        req.add_header("x-api-key", ANTHROPIC_API_KEY)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("Content-Type", "application/json")
        try:
            ctx = ssl.create_default_context()
            resp = json.load(urllib.request.urlopen(req, context=ctx, timeout=60))
            return resp.get("content", [{}])[0].get("text", "")
        except Exception as e:
            print(f"Claude error: {e}")
            return ""

    # ==================== DATA GATHERING ====================

    def get_todays_meetings(self) -> List[Dict]:
        start = self.now.replace(hour=0, minute=0, second=0).isoformat()
        end = self.now.replace(hour=23, minute=59, second=59).isoformat()
        endpoint = f"google-calendar/calendar/v3/calendars/primary/events?timeMin={start}&timeMax={end}&singleEvents=true&orderBy=startTime"
        result = self.maton_request(endpoint)
        if result and 'items' in result:
            return result['items']
        return []

    def get_tomorrows_meetings(self) -> List[Dict]:
        tomorrow = self.now + timedelta(days=1)
        start = tomorrow.replace(hour=0, minute=0, second=0).isoformat()
        end = tomorrow.replace(hour=23, minute=59, second=59).isoformat()
        endpoint = f"google-calendar/calendar/v3/calendars/primary/events?timeMin={start}&timeMax={end}&singleEvents=true&orderBy=startTime"
        result = self.maton_request(endpoint)
        if result and 'items' in result:
            return result['items']
        return []

    def get_open_linear_tasks(self) -> Dict[str, List[Dict]]:
        """Get all open tasks grouped by team."""
        teams = {"TRC Admin": "TRC", "TRC Clients": "TRCC"}
        all_tasks = {}
        for team_name, team_key in teams.items():
            query = """{
              issues(filter: {
                team: { key: { eq: "%s" } }
              }, orderBy: updatedAt, first: 50) {
                nodes {
                  identifier title
                  state { name type }
                  priority priorityLabel
                  assignee { name }
                  dueDate
                  url
                }
              }
            }""" % team_key
            headers = {"Authorization": f"Bearer {MATON_API_KEY}", "Content-Type": "application/json"}
            result = self.api_request(LINEAR_API, headers, {"query": query})
            if result and result.get('data', {}).get('issues'):
                tasks = result['data']['issues'].get('nodes', [])
                # Filter out completed and canceled client-side
                tasks = [t for t in tasks if t.get('state', {}).get('type') not in ('completed', 'canceled')]
                if tasks:
                    all_tasks[team_name] = tasks
        return all_tasks

    def get_overdue_tasks(self, all_tasks: Dict[str, List[Dict]]) -> List[Dict]:
        """Extract overdue tasks from all open tasks."""
        today = self.now.strftime("%Y-%m-%d")
        overdue = []
        for team, tasks in all_tasks.items():
            for t in tasks:
                due = t.get('dueDate')
                if due and due < today:
                    t['_team'] = team
                    overdue.append(t)
        return overdue

    def get_recent_meeting_notes(self, days: int = 3) -> List[Dict]:
        """Get meeting notes from the knowledge base created in the last N days."""
        # Search for recent docs in the Meeting Notes folder
        cutoff = (self.now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        q = urllib.parse.quote(
            f"'{MEETING_NOTES_FOLDER}' in parents and modifiedTime > '{cutoff}'"
        )
        fields = urllib.parse.quote("files(id,name,modifiedTime,webViewLink)")
        order = urllib.parse.quote("modifiedTime desc")
        endpoint = f"google-drive/drive/v3/files?q={q}&fields={fields}&orderBy={order}&pageSize=10"
        result = self.maton_request(endpoint)
        if result and 'files' in result:
            return result['files']
        return []

    def read_doc_content(self, doc_id: str, max_chars: int = 3000) -> str:
        """Read content from a Google Doc."""
        endpoint = f"google-docs/v1/documents/{doc_id}"
        result = self.maton_request(endpoint)
        if not result or 'body' not in result:
            return ""
        text_parts = []
        total = 0
        for element in result['body'].get('content', []):
            if 'paragraph' in element:
                for te in element['paragraph'].get('elements', []):
                    if 'textRun' in te:
                        chunk = te['textRun'].get('content', '')
                        text_parts.append(chunk)
                        total += len(chunk)
                        if total > max_chars:
                            break
            if total > max_chars:
                break
        return ''.join(text_parts)

    def extract_attendee_names(self, meetings: List[Dict]) -> List[str]:
        """Extract unique attendee names/emails from meetings, excluding TRC domain."""
        names = set()
        for m in meetings:
            for a in m.get('attendees', []):
                email = a.get('email', '')
                if email and '@thereadyconsult.com' not in email:
                    # Extract name from email or displayName
                    display = a.get('displayName', '')
                    if display:
                        names.add(display)
                    else:
                        names.add(email.split('@')[0].replace('.', ' ').title())
                elif email and '@thereadyconsult.com' in email:
                    name = a.get('displayName', email.split('@')[0])
                    names.add(name)
        return list(names)

    def search_linear_for_attendees(self, names: List[str]) -> Dict[str, List[Dict]]:
        """Search Linear for tasks related to meeting attendees."""
        results = {}
        for name in names[:5]:
            query_str = name.replace('"', '')
            query = """{
              issueSearch(query: "%s", first: 10) {
                nodes {
                  identifier title
                  state { name type }
                  priorityLabel
                  assignee { name }
                  dueDate
                  url
                }
              }
            }""" % query_str
            headers = {"Authorization": f"Bearer {MATON_API_KEY}", "Content-Type": "application/json"}
            result = self.api_request(LINEAR_API, headers, {"query": query})
            data = result.get('data') if result else None
            if data and data.get('issueSearch'):
                tasks = data['issueSearch'].get('nodes', [])
                tasks = [t for t in tasks if t.get('state', {}).get('type') not in ('completed', 'canceled')]
                if tasks:
                    results[name] = tasks
        return results

    def search_notion_for_attendees(self, names: List[str]) -> Dict[str, List[Dict]]:
        """Search Notion for pages related to meeting attendees."""
        results = {}
        for name in names[:5]:
            result = self.maton_request(
                "notion/v1/search", method="POST",
                data={"query": name, "page_size": 5},
                extra_headers={"Notion-Version": "2022-06-28"}
            )
            if result and result.get('results'):
                pages = []
                for r in result['results'][:3]:
                    title = ""
                    props = r.get('properties', {})
                    for key, val in props.items():
                        if val.get('type') == 'title':
                            title_arr = val.get('title', [])
                            if title_arr:
                                title = title_arr[0].get('plain_text', '')
                    if not title:
                        title = r.get('url', 'Untitled')
                    pages.append({'title': title, 'url': r.get('url', '')})
                if pages:
                    results[name] = pages
        return results

    def search_gmail_for_attendees(self, names: List[str]) -> Dict[str, List[Dict]]:
        """Search Gmail for recent threads with meeting attendees."""
        results = {}
        for name in names[:5]:
            q = urllib.parse.quote(f"from:{name} OR to:{name}")
            endpoint = f"google-mail/gmail/v1/users/me/messages?q={q}&maxResults=5"
            result = self.maton_request(endpoint)
            threads = []
            if result and 'messages' in result:
                for m in result['messages'][:3]:
                    detail = self.maton_request(
                        f"google-mail/gmail/v1/users/me/messages/{m['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
                    )
                    if detail:
                        hdrs = {h['name']: h['value'] for h in detail.get('payload', {}).get('headers', [])}
                        threads.append({
                            'subject': hdrs.get('Subject', ''),
                            'from': hdrs.get('From', ''),
                            'date': hdrs.get('Date', ''),
                            'snippet': detail.get('snippet', '')[:150]
                        })
            if threads:
                results[name] = threads
        return results

    def search_drive_for_attendees(self, names: List[str]) -> List[Dict]:
        """Search Drive Meeting Notes folder for docs mentioning attendees."""
        all_files = []
        seen = set()
        for name in names[:5]:
            q = urllib.parse.quote(
                f"name contains '{name}' and '{MEETING_NOTES_FOLDER}' in parents"
            )
            fields = urllib.parse.quote("files(id,name,modifiedTime,webViewLink)")
            endpoint = f"google-drive/drive/v3/files?q={q}&fields={fields}&pageSize=5"
            result = self.maton_request(endpoint)
            if result and 'files' in result:
                for f in result['files']:
                    if f['id'] not in seen:
                        seen.add(f['id'])
                        all_files.append(f)
        return all_files

    def search_fireflies_for_attendees(self, names: List[str]) -> List[Dict]:
        """Search Fireflies for past transcripts with attendees."""
        fireflies_key = os.environ.get("FIREFLIES_API_KEY", "")
        if not fireflies_key:
            return []
        query = """{ transcripts(limit: 20) {
            id title date duration
            organizer_email participants
            summary { overview action_items }
        } }"""
        headers = {
            "Authorization": f"Bearer {fireflies_key}",
            "Content-Type": "application/json",
            "User-Agent": "OpenClaw/1.0"
        }
        result = self.api_request("https://api.fireflies.ai/graphql", headers, {"query": query})
        if not result or not result.get('data', {}).get('transcripts'):
            return []
        
        matches = []
        name_lower = [n.lower() for n in names]
        for t in result['data']['transcripts']:
            title = (t.get('title') or '').lower()
            participants = [p.lower() for p in (t.get('participants') or [])]
            for n in name_lower:
                if n in title or any(n in p for p in participants):
                    matches.append(t)
                    break
        return matches[:5]

    def get_recent_emails(self, hours: int = 24) -> List[Dict]:
        """Get important unread emails from the last N hours."""
        endpoint = "google-mail/gmail/v1/users/me/messages?q=is:unread+newer_than:1d&maxResults=10"
        result = self.maton_request(endpoint)
        messages = []
        if result and 'messages' in result:
            for m in result['messages'][:10]:
                detail = self.maton_request(
                    f"google-mail/gmail/v1/users/me/messages/{m['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From"
                )
                if detail:
                    headers = {h['name']: h['value'] for h in detail.get('payload', {}).get('headers', [])}
                    messages.append({
                        'subject': headers.get('Subject', '(no subject)'),
                        'from': headers.get('From', ''),
                        'snippet': detail.get('snippet', ''),
                        'id': m['id']
                    })
        return messages

    # ==================== BRIEF GENERATION ====================

    def generate_digest(self) -> Dict[str, str]:
        """Generate both email and Slack versions of the daily digest."""
        print("Gathering data...")

        todays_meetings = self.get_todays_meetings()
        tomorrows_meetings = self.get_tomorrows_meetings()
        open_tasks = self.get_open_linear_tasks()
        overdue = self.get_overdue_tasks(open_tasks)
        todays_notes = self.get_recent_meeting_notes(days=1)
        recent_notes = self.get_recent_meeting_notes(days=3)
        unread_emails = self.get_recent_emails()

        # Read content from today's meeting notes (full content — these are the focus)
        todays_notes_content = []
        today_ids = set()
        for note in todays_notes[:5]:
            content = self.read_doc_content(note['id'], max_chars=4000)
            todays_notes_content.append({
                'name': note.get('name', ''),
                'link': note.get('webViewLink', ''),
                'modified': note.get('modifiedTime', ''),
                'content': content
            })
            today_ids.add(note['id'])

        # Read older recent notes (shorter, for context)
        older_notes_content = []
        for note in recent_notes[:5]:
            if note['id'] not in today_ids:
                content = self.read_doc_content(note['id'], max_chars=1500)
                older_notes_content.append({
                    'name': note.get('name', ''),
                    'link': note.get('webViewLink', ''),
                    'modified': note.get('modifiedTime', ''),
                    'content': content
                })

        # Research tomorrow's meeting attendees across all knowledge bases
        tomorrow_names = self.extract_attendee_names(tomorrows_meetings)
        tomorrow_linear = {}
        tomorrow_notion = {}
        tomorrow_gmail = {}
        tomorrow_drive = []
        tomorrow_fireflies = []
        if tomorrow_names:
            print(f"  Researching tomorrow's attendees: {', '.join(tomorrow_names)}")
            tomorrow_linear = self.search_linear_for_attendees(tomorrow_names)
            tomorrow_notion = self.search_notion_for_attendees(tomorrow_names)
            tomorrow_gmail = self.search_gmail_for_attendees(tomorrow_names)
            tomorrow_drive = self.search_drive_for_attendees(tomorrow_names)
            tomorrow_fireflies = self.search_fireflies_for_attendees(tomorrow_names)

        print(f"  Meetings today: {len(todays_meetings)}")
        print(f"  Meetings tomorrow: {len(tomorrows_meetings)}")
        print(f"  Open tasks: {sum(len(t) for t in open_tasks.values())}")
        print(f"  Overdue tasks: {len(overdue)}")
        print(f"  Today's meeting notes: {len(todays_notes_content)}")
        print(f"  Older meeting notes: {len(older_notes_content)}")
        print(f"  Tomorrow prep — Linear: {sum(len(v) for v in tomorrow_linear.values())}, Notion: {sum(len(v) for v in tomorrow_notion.values())}, Gmail: {sum(len(v) for v in tomorrow_gmail.values())}, Drive: {len(tomorrow_drive)}, Fireflies: {len(tomorrow_fireflies)}")
        print(f"  Unread emails: {len(unread_emails)}")

        # Build raw context for Claude
        raw = f"DATE: {self.today_str} ({self.now.strftime('%A')})\n\n"

        # Today's meetings
        raw += "TODAY'S MEETINGS:\n"
        if todays_meetings:
            for m in todays_meetings:
                start = m.get('start', {}).get('dateTime', '')
                try:
                    dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    time_str = dt.strftime('%I:%M %p')
                except:
                    time_str = start
                attendees = [a.get('email', '') for a in m.get('attendees', [])]
                raw += f"- {time_str}: {m.get('summary', 'No title')} | Attendees: {', '.join(attendees)}\n"
        else:
            raw += "- No meetings today\n"
        raw += "\n"

        # Tomorrow's meetings
        raw += "TOMORROW'S MEETINGS:\n"
        if tomorrows_meetings:
            for m in tomorrows_meetings:
                start = m.get('start', {}).get('dateTime', '')
                try:
                    dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    time_str = dt.strftime('%I:%M %p')
                except:
                    time_str = start
                attendees = [a.get('displayName') or a.get('email', '') for a in m.get('attendees', [])]
                desc = m.get('description', '')
                raw += f"- {time_str}: {m.get('summary', 'No title')} | Attendees: {', '.join(attendees)}\n"
                if desc:
                    # Clean metadata from description
                    clean_desc = desc[:300].replace('<br>', ' ').replace('\n', ' ').strip()
                    raw += f"  Description: {clean_desc}\n"
        else:
            raw += "- No meetings tomorrow\n"
        raw += "\n"

        # Tomorrow's prep context from knowledge bases
        if tomorrow_names:
            raw += "TOMORROW'S MEETING PREP CONTEXT:\n\n"

            # Linear tasks for tomorrow's attendees
            if tomorrow_linear:
                raw += "Linear Tasks (related to tomorrow's attendees):\n"
                for name, tasks in tomorrow_linear.items():
                    raw += f"  {name}:\n"
                    for t in tasks[:5]:
                        assignee = t.get('assignee', {})
                        aname = assignee.get('name', 'Unassigned') if assignee else 'Unassigned'
                        raw += f"  - {t.get('identifier')} {t.get('title')} | {t.get('state', {}).get('name', '')} | {aname}"
                        if t.get('dueDate'):
                            raw += f" | Due: {t['dueDate']}"
                        raw += "\n"
                raw += "\n"

            # Notion pages
            if tomorrow_notion:
                raw += "Notion Pages (related to tomorrow's attendees):\n"
                for name, pages in tomorrow_notion.items():
                    for p in pages:
                        raw += f"  - {p.get('title', 'Untitled')} ({name}) — {p.get('url', '')}\n"
                raw += "\n"

            # Gmail threads
            if tomorrow_gmail:
                raw += "Recent Email Threads (with tomorrow's attendees):\n"
                for name, threads in tomorrow_gmail.items():
                    raw += f"  {name}:\n"
                    for t in threads:
                        raw += f"  - {t.get('subject', 'No subject')} | {t.get('date', '')}\n"
                        if t.get('snippet'):
                            raw += f"    {t['snippet']}\n"
                raw += "\n"

            # Past meeting notes from Drive
            if tomorrow_drive:
                raw += "Past Meeting Notes (from knowledge base, related to tomorrow's attendees):\n"
                for doc in tomorrow_drive[:3]:
                    raw += f"  - {doc.get('name', '')} — {doc.get('webViewLink', '')}\n"
                    content = self.read_doc_content(doc['id'], max_chars=1500)
                    if content:
                        raw += f"    {content[:1000]}\n"
                raw += "\n"

            # Fireflies past transcripts
            if tomorrow_fireflies:
                raw += "Past Meetings (Fireflies transcripts with tomorrow's attendees):\n"
                for t in tomorrow_fireflies:
                    date_str = t.get('date', '')
                    raw += f"  - {t.get('title', '')} ({date_str})\n"
                    overview = t.get('summary', {}).get('overview', '')
                    if overview:
                        raw += f"    Summary: {overview[:500]}\n"
                    items = t.get('summary', {}).get('action_items', [])
                    if items:
                        raw += f"    Action items: {', '.join(items[:5]) if isinstance(items, list) else str(items)[:300]}\n"
                raw += "\n"

        # Overdue tasks
        if overdue:
            raw += "OVERDUE TASKS:\n"
            for t in overdue:
                raw += f"- {t.get('identifier')} {t.get('title')} | Due: {t.get('dueDate')} | Assigned: {t.get('assignee', {}).get('name', 'Unassigned')} | Team: {t.get('_team', '')}\n"
            raw += "\n"

        # Open tasks by team
        raw += "OPEN TASKS BY TEAM:\n"
        for team, tasks in open_tasks.items():
            raw += f"\n{team}:\n"
            for t in tasks[:15]:
                assignee = t.get('assignee', {})
                assignee_name = assignee.get('name', 'Unassigned') if assignee else 'Unassigned'
                raw += f"- {t.get('identifier')} {t.get('title')} | {t.get('state', {}).get('name', '')} | {t.get('priorityLabel', '')} | {assignee_name}"
                if t.get('dueDate'):
                    raw += f" | Due: {t['dueDate']}"
                raw += "\n"
        raw += "\n"

        # Today's meeting notes — full content, this is the core of the recap
        raw += "TODAY'S MEETING NOTES (from knowledge base):\n"
        if todays_notes_content:
            for nc in todays_notes_content:
                raw += f"\n=== {nc['name']} ===\n"
                raw += f"Link: {nc['link']}\n"
                if nc['content']:
                    raw += nc['content'][:4000] + "\n"
        else:
            raw += "No meeting notes created today\n"
        raw += "\n"

        # Older recent notes — shorter, for context
        if older_notes_content:
            raw += "RECENT MEETING NOTES (last few days, for context):\n"
            for nc in older_notes_content:
                raw += f"\n--- {nc['name']} ---\n"
                raw += f"Link: {nc['link']}\n"
                if nc['content']:
                    raw += nc['content'][:1500] + "\n"
            raw += "\n"

        # Unread emails
        raw += "UNREAD EMAILS (LAST 24H):\n"
        if unread_emails:
            for e in unread_emails[:8]:
                raw += f"- From: {e['from']} | Subject: {e['subject']}\n  {e.get('snippet', '')[:150]}\n"
        else:
            raw += "- Inbox clear\n"

        # Generate email version
        email_html = self.generate_email_digest(raw)

        # Generate Slack version
        slack_text = self.generate_slack_digest(raw)

        return {'email': email_html, 'slack': slack_text, 'raw': raw}

    def generate_email_digest(self, raw_context: str) -> str:
        system = """You are a chief of staff preparing an end-of-day post-call briefing for Shawn, CEO of The Ready Consult.

This goes out at 7 PM PST. It's a wrap-up of the day — what happened in today's meetings, what needs follow-up, and what's coming tomorrow.

OUTPUT FORMAT: HTML email. Use these tags only: <p>, <b>, <br>, <ul>, <li>, <hr>, <a href>. No markdown. No emoji.

STRUCTURE:
1. <p><b>End of Day Recap — [Day, Date]</b></p>
2. <hr>
3. <p><b>Today's Meetings</b></p> — What happened today. For each meeting, summarize key outcomes, decisions, and commitments from the meeting notes. If meeting notes exist, pull out the substance — who said what, what was agreed, what's pending. Link to the full doc. If no meetings, say "No meetings today."
4. <hr>
5. <p><b>Action Items from Today</b></p> — Consolidate all action items from today's meeting notes. Each as <li> with task, owner, and due date. These are the things that need to happen next.
6. <hr>
7. <p><b>Overdue Items</b></p> — Only if there are overdue tasks. Each as <li> with identifier, title, assignee, how many days overdue.
8. <p><b>Open Tasks</b></p> — Grouped by team. Top priorities first. Each as <li> with identifier, title, status, assignee. Cap at 10 per team.
9. <hr>
10. <p><b>Tomorrow's Schedule</b></p> — What's coming. Each meeting with time, title, attendees.
11. <hr>
12. <p><b>Inbox Highlights</b></p> — Important unread emails or "Inbox clear."
13. <hr>
14. <p>-- Moltbot</p>

RULES:
- Lead with what happened today — meetings and their outcomes are the most important section.
- For meeting insights, be specific. Reference names, numbers, commitments, deadlines. Not vague summaries.
- Action items should be concrete and actionable with clear owners.
- Be concise. This should take 60 seconds to read.
- Use <ul><li> for all lists.
- Every section must appear. Use "N/A" or "None" if empty.
- No markdown, no asterisks, no backticks."""

        result = self.claude_request(system, f"Generate the end-of-day briefing email from this data:\n\n{raw_context}")
        return result

    def generate_slack_digest(self, raw_context: str) -> str:
        system = """You are a chief of staff. Write a concise Slack end-of-day recap for Shawn.

FORMAT: Slack mrkdwn ONLY. This is NOT standard Markdown.

SLACK MRKDWN RULES (follow exactly):
- Bold: *text* (SINGLE asterisk, NOT double **)
- Italic: _text_ (must be paired, no orphan underscores)
- Bullets: use the bullet character • at the start of each line
- Links: <url|text>
- Line breaks: regular newlines
- Dividers: use a blank line between sections, NOT --- or ———
- NEVER use ** (double asterisks) — Slack renders them as literal text
- NEVER use # headers — Slack does not support them
- NEVER use orphan underscores at the start or end of lines

STRUCTURE:

*End of Day — [Day, Date]*

*Today's Calls*
[meeting title] — [time]
With: [attendee names]
• [key decision or outcome 1]
• [key decision or outcome 2]

*Action Items*
• [task] — [owner] — Due: [date]

*Overdue* (only if any exist)
• [identifier]: [title] — [days] days overdue

*Tomorrow*
[meeting title] — [time]
With: [attendee names]
• [what to prep or know before walking in]

*Recent meetings*
• [meeting title] ([date])

*Related docs*
• [doc title] (updated [date])

*Inbox*
• [count] unread — [most important subject]

RULES:
- Today's Calls and Tomorrow are the two most important sections.
- Be specific and actionable — names, numbers, dates. Not vague summaries.
- Max 30 lines total. Easy to scan on a phone.
- Skip any section with nothing to report.
- No emoji. No walls of text. No double asterisks. No stray underscores.
- Before outputting, re-read your message. If you see ** anywhere, replace with single *. If you see orphan _ characters, remove them."""

        result = self.claude_request(system, f"Generate the Slack end-of-day recap from this data:\n\n{raw_context}")
        # Post-process: fix any double asterisks that Claude might still output
        if result:
            import re
            # Replace **text** with *text*
            result = re.sub(r'\*\*(.+?)\*\*', r'*\1*', result)
            # Remove orphan underscores at start of lines
            result = re.sub(r'^_([^_])', r'\1', result, flags=re.MULTILINE)
            # Remove orphan underscores before newlines
            result = re.sub(r'([^_])_$', r'\1', result, flags=re.MULTILINE)
            # Remove stray --- or ——— dividers
            result = re.sub(r'^[-—]{3,}$', '', result, flags=re.MULTILINE)
        return result

    # ==================== DELIVERY ====================

    def send_email(self, html_body: str) -> bool:
        day_str = self.now.strftime("%A")
        subject = f"End of Day Recap — {day_str}, {self.today_str}"

        msg = MIMEText(html_body, 'html', 'utf-8')
        msg['To'] = SHAWN_EMAIL
        msg['Cc'] = CC_EMAIL
        msg['From'] = SHAWN_EMAIL
        msg['Subject'] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = self.maton_request(
            "google-mail/gmail/v1/users/me/messages/send",
            method="POST", data={"raw": raw}
        )
        if result and result.get('id'):
            print(f"Email sent: {subject}")
            return True
        print(f"Email failed: {subject}")
        return False

    def send_slack(self, text: str) -> bool:
        data = json.dumps({"channel": SLACK_CHANNEL, "text": text, "mrkdwn": True}).encode()
        req = urllib.request.Request('https://slack.com/api/chat.postMessage', data=data, method='POST')
        req.add_header('Authorization', f'Bearer {SLACK_BOT_TOKEN}')
        req.add_header('Content-Type', 'application/json')
        try:
            resp = json.load(urllib.request.urlopen(req))
            if resp.get('ok'):
                print("Slack DM sent")
                return True
            print(f"Slack failed: {resp.get('error')}")
            return False
        except Exception as e:
            print(f"Slack error: {e}")
            return False

    # ==================== MAIN ====================

    def run(self):
        if not MATON_API_KEY:
            print("ERROR: MATON_API_KEY not set")
            return

        print(f"=== Daily Digest — {self.today_str} ===")

        digest = self.generate_digest()

        if digest.get('slack'):
            self.send_slack(digest['slack'])
        else:
            print("No Slack content generated")

        print("=== Done ===")


if __name__ == "__main__":
    DailyDigest().run()
