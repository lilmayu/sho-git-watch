import os.path
import re
import requests
import time
import sys
from datetime import datetime
from dataclasses import dataclass
from collections import deque
from typing import Any

from .config import Config

@dataclass(frozen=True)
class PollResponse:
    etag: str | None
    poll_interval: int

COMPONENTS_V2_FLAG = 1 << 15
ALERT_COLOR = 10878867

def _divider() -> dict[str, Any]:
    return {'type': 14, 'divider': True, 'spacing': 1}

def _text(content: str) -> dict[str, Any]:
    return {'type': 10, 'content': content}

QUOTE_MAX_LINES = 15
QUOTE_MAX_CHARS = 600

def _quote(body: str) -> str:
    body = body.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not body:
        return ''

    lines = []
    prev_blank = False
    for line in body.split('\n'):
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        lines.append(line)
        prev_blank = is_blank

    truncated = len(lines) > QUOTE_MAX_LINES
    text = '\n'.join(lines[:QUOTE_MAX_LINES])

    if len(text) > QUOTE_MAX_CHARS:
        text = text[:QUOTE_MAX_CHARS].rstrip()
        truncated = True

    quoted = '\n'.join(f'> {line}' for line in text.split('\n'))
    if truncated:
        quoted += '\n> *(truncated)*'
    return quoted

MEDIA_LIMIT = 3
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.webm', '.m4v', '.ogg')

VIDEO_TAG_RE = re.compile(r'<video\b[^>]*>.*?</video>', re.IGNORECASE | re.DOTALL)
IMG_TAG_RE = re.compile(r'<img\b[^>]*?\bsrc=["\']([^"\']+)["\'][^>]*?/?>', re.IGNORECASE)
MD_IMG_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)[^)]*\)')
SRC_ATTR_RE = re.compile(r'\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
BARE_ATTACHMENT_RE = re.compile(r'https://github\.com/user-attachments/assets/[\w-]+')

def _extract_media(body: str) -> tuple[str, list[dict[str, str]]]:
    videos = []
    images = []
    unknown = []

    def take_video(match: re.Match) -> str:
        src = SRC_ATTR_RE.search(match.group(0))
        if src:
            videos.append(src.group(1))
        return ''

    body = VIDEO_TAG_RE.sub(take_video, body)

    def take_img(match: re.Match) -> str:
        images.append(match.group(1))
        return ''

    body = IMG_TAG_RE.sub(take_img, body)

    def take_md_image(match: re.Match) -> str:
        url = match.group(1)
        (videos if url.lower().endswith(VIDEO_EXTENSIONS) else images).append(url)
        return ''

    body = MD_IMG_RE.sub(take_md_image, body)

    def take_bare_attachment(match: re.Match) -> str:
        unknown.append(match.group(0))
        return ''

    body = BARE_ATTACHMENT_RE.sub(take_bare_attachment, body)

    media = (
        [{'type': 'video', 'url': url} for url in videos]
        + [{'type': 'image', 'url': url} for url in images]
        + [{'type': 'unknown', 'url': url} for url in unknown]
    )
    return body, media[:MEDIA_LIMIT]

def _media_gallery(media: list[dict[str, str]]) -> dict[str, Any]:
    return {
        'type': 12,
        'items': [{'media': {'url': m['url']}, 'description': None, 'spoiler': False} for m in media]
    }

def _container(header: str, body: str, timestamp: int, media: list[dict[str, str]] | None = None) -> dict[str, Any]:
    components = [
        _text(f'## ⚠️ {header}'),
        _divider(),
        _text(body)
    ]
    if media:
        components.append(_media_gallery(media))
    components.append(_divider())
    components.append(_text(f'-# <t:{timestamp}:f>'))

    return {
        'type': 17,
        'accent_color': ALERT_COLOR,
        'spoiler': False,
        'components': components
    }

def _commit_body(commit: dict[str, Any]) -> str:
    title, _, rest = commit['commit']['message'].partition('\n')
    quoted = _quote(rest)
    author = commit['author']['login'] if commit.get('author') else commit['commit']['author']['name']
    committer = commit['committer']['login'] if commit.get('committer') else commit['commit']['committer']['name']

    lines = [f'### [{title}]({commit["html_url"]})']
    if quoted:
        lines.append(quoted)
    lines.append(f'Created by `{author}`')
    if committer != author:
        lines.append(f'Commit by `{committer}`')
    return '\n'.join(lines)

def _pull_request_body(pr: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    cleaned_body, media = _extract_media(pr.get('body') or '')
    quoted = _quote(cleaned_body)

    lines = [
        f'### [{pr["title"]}]({pr["html_url"]})',
        quoted if quoted else '*No description*',
        f'Created by `{pr["user"]["login"]}`'
    ]
    return '\n'.join(lines), media

def _generic_body(payload: dict[str, Any], event_type: str, actor: str) -> tuple[str, list[dict[str, str]]]:
    title = None
    url = None
    raw_body = None

    if 'comment' in payload:
        raw_body = payload['comment'].get('body') or ''
        url = payload['comment'].get('html_url')
        if 'issue' in payload:
            title = payload['issue'].get('title')
            url = payload['issue'].get('html_url', url)
    elif 'issue' in payload:
        title = payload['issue'].get('title')
        url = payload['issue'].get('html_url')
        raw_body = payload['issue'].get('body') or ''
    elif 'release' in payload:
        title = payload['release'].get('name') or payload['release'].get('tag_name')
        url = payload['release'].get('html_url')
        raw_body = payload['release'].get('body') or ''
    elif 'forkee' in payload:
        title = payload['forkee'].get('full_name')
        url = payload['forkee'].get('html_url')
    elif event_type in ('create', 'delete') and 'ref' in payload:
        title = f"{payload.get('ref_type', 'ref')} `{payload['ref']}`"

    lines = []
    if title and url:
        lines.append(f'### [{title}]({url})')
    elif title:
        lines.append(f'### {title}')

    media = []
    if raw_body is not None:
        cleaned_body, media = _extract_media(raw_body)
        quoted = _quote(cleaned_body)
        lines.append(quoted if quoted else '*No description*')

    lines.append(f'By `{actor}`')
    return '\n'.join(lines), media

EVENT_HEADERS = {
    'commit_comment': 'New commit comment in {repo}',
    'discussion': 'New discussion in {repo}',
    'discussion_comment': 'New discussion comment in {repo}',
    'fork': 'New fork of {repo}',
    'issue_comment': 'New issue comment in {repo}',
    'issues': 'New issue in {repo}',
    'member': 'New member in {repo}',
    'public': '{repo} was made public',
    'pull_request_review': 'New pull request review in {repo}',
    'pull_request_review_comment': 'New pull request review comment in {repo}',
    'release': 'New release in {repo}',
    'watch': 'New star on {repo}'
}

def _generic_header(event_type: str, payload: dict[str, Any], repo_name: str | None) -> str:
    repo = repo_name or 'repo'

    if event_type == 'create':
        return f"New {payload.get('ref_type', 'ref')} in {repo}"
    if event_type == 'delete':
        return f"Deleted {payload.get('ref_type', 'ref')} in {repo}"

    template = EVENT_HEADERS.get(event_type, '{event} event in {repo}')
    return template.format(repo=repo, event=event_type.replace('_', ' '))

def _send_containers(webhook_url: str, containers: list[dict[str, Any]]) -> requests.Response:
    return requests.post(
        webhook_url,
        params={'with_components': 'true'},
        json={'flags': COMPONENTS_V2_FLAG, 'components': containers}
    )

class Webhook:
    def __init__(self, config: Config) -> None:
        if config.GH_TOKEN == '':
            print("Empty GitHub Token in the configuration!")
            exit()

        self.repo_cache: dict[str, dict[str, Any]] = {}
        self.gh_headers = {
            'Authorization': f'Bearer {config.GH_TOKEN}'
        }
        self.seen_events = deque(maxlen=300)
        self.first_run = True

    def write_state(self) -> None:
        try:
            with open('.state', 'w') as f:
                f.write(str(round(time.time())))
        except Exception as e:
            print(f'Failed to write state. {type(e)}')

    def read_state(self) -> int:
        try:
            with open('.state') as f:
                return int(f.read().strip())
        except Exception as e:
            print(f'Failed to read state. {type(e)}')
            return 0

    def poll(self, etag: str | None) -> PollResponse:
        print(f'Fetching with etag {etag}')
        eventsreq = requests.get(config.EVENT_API, headers=self.gh_headers if not etag else {**self.gh_headers, 'If-None-Match': etag})

        poll_interval = max(int(eventsreq.headers.get('X-Poll-Interval', config.POLL_INTERVAL)), config.POLL_INTERVAL)

        if eventsreq.status_code == 304:
            print('No new events.')
            return PollResponse(etag, poll_interval)

        if eventsreq.status_code not in (200, 304):
            raise Exception(f'Failed to fetch events ({eventsreq.status_code}): {eventsreq.text}')

        etag = eventsreq.headers.get('ETag', None)

        old_state = 0
        if self.first_run:
            old_state = self.read_state()

        for event in eventsreq.json()[::-1]:
            if event['id'] in self.seen_events:
                continue
            self.seen_events.append(event['id']) # TODO: Should this be moved to the bottom when the webhook is actually sent, so errors will retry events

            if self.first_run:
                if old_state:
                    if round(datetime.fromisoformat(event['created_at']).timestamp()) < old_state:
                        continue # This event happened while the script was last running; skip event as it's most likely already sent.
                elif config.SKIP_INITIAL_EVENTS:
                    continue # No .state file yet, this is the very first run ever; skip the backlog instead of replaying it.

            # Really we should be fetching the user object here
            # However, the only new property Discord needs is html_url, which can be derived from the partial user
            event['actor']['html_url'] = event['actor']['url'].replace('api.github.com/users', 'github.com')

            EVENT_TYPES = {
                'CommitCommentEvent': 'commit_comment',
                'CreateEvent': 'create',
                'DeleteEvent': 'delete',
                'DiscussionEvent': 'discussion',
                'ForkEvent': 'fork',
                'GollumEvent': 'gollum',
                'IssueCommentEvent': 'issue_comment',
                'IssuesEvent': 'issues',
                'MemberEvent': 'member',
                'PublicEvent': 'public',
                'PullRequestEvent': 'pull_request',
                'PullRequestReviewEvent': 'pull_request_review',
                'PullRequestReviewCommentEvent': 'pull_request_review_comment',
                'PushEvent': 'push',
                'ReleaseEvent': 'release',
                'WatchEvent': 'watch',
            }

            event_type = EVENT_TYPES.get(event['type'], None)

            if not event_type:
                print('Unhandled event type', event['type'])
                continue

            if event_type in config.EVENT_BLACKLIST:
                print('Blacklisted event type', event_type)
                continue

            if config.USER_WHITELIST:
                if event['actor']['login'] not in config.USER_WHITELIST:
                    print('Unwhitelisted user login', event['actor']['login'])
                    continue

            # TODO: Refresh repo caches every once in a while
            if 'repo' in event:
                if event['repo']['id'] not in self.repo_cache:
                    reporeq = requests.get(event['repo']['url'], headers=self.gh_headers)
                    if reporeq.status_code != 200:
                        print(f'Failed to fetch repo at {event["repo"]["url"]} ({reporeq.status_code}): {reporeq.text}')
                        continue
                    repo = reporeq.json()
                    self.repo_cache[event['repo']['id']] = repo
                    event['repo'] = repo
                else:
                    event['repo'] = self.repo_cache[event['repo']['id']]

                if event['repo']['full_name'] in config.REPO_BLACKLIST:
                    print('Skipping blacklisted repo', event['repo']['full_name'])
                    continue

            timestamp = round(datetime.fromisoformat(event['created_at']).timestamp())
            repo_name = event['repo']['name'] if 'repo' in event else None

            if event_type == 'pull_request':
                prreq = requests.get(event['payload']['pull_request']['url'], headers=self.gh_headers)
                if prreq.status_code != 200:
                    print(event['payload']['pull_request']['url'])
                    print(prreq.text)
                    sys.exit(1)
                pr = prreq.json()

                action = event['payload']['action']
                action_text = {
                    'opened': 'New',
                    'reopened': 'Reopened',
                    'closed': 'Merged' if pr.get('merged') else 'Closed'
                }.get(action, action.capitalize())

                header = f'{action_text} {repo_name} Pull Request'
                body, media = _pull_request_body(pr)
                container = _container(header, body, timestamp, media)

                r = _send_containers(config.DISCORD_WEBHOOK, [container])
                print(f'Sent {event_type} -> {r.status_code}: {r.text}')
                continue

            if event_type == 'push':
                commitsreq = requests.get(f"https://api.github.com/repos/{event['repo']['full_name']}/compare/{event['payload']['before']}...{event['payload']['head']}", headers=self.gh_headers)
                if commitsreq.status_code != 200:
                    raise Exception(f'Failed to fetch commit compare at https://api.github.com/repos/{event["repo"]["full_name"]}/compare/{event["payload"]["before"]}...{event["payload"]["head"]} ({commitsreq.status_code}): {commitsreq.text}')
                commits = commitsreq.json()

                header = f'New {repo_name} commit'
                containers = [_container(header, _commit_body(c), timestamp) for c in commits['commits']]

                if not containers:
                    continue

                r = _send_containers(config.DISCORD_WEBHOOK, containers)
                print(f'Sent {event_type} -> {r.status_code}: {r.text}')
                continue

            header = _generic_header(event_type, event['payload'], repo_name)
            body, media = _generic_body(event['payload'], event_type, event['actor']['display_login'])
            container = _container(header, body, timestamp, media)

            r = _send_containers(config.DISCORD_WEBHOOK, [container])
            print(f'Sent {event_type} -> {r.status_code}: {r.text}')

        self.first_run = False
        self.write_state()
        return PollResponse(etag, poll_interval)

if __name__ == '__main__':
    if not os.path.exists('config.toml'):
        print("config.toml does not exist! run cp config.example.toml config.toml and edit it.")
        exit()

    config = Config.from_toml('config.toml')
    webhook = Webhook(config)

    etag = None
    while True:
        try:
            pollresponse = webhook.poll(etag)
            etag = pollresponse.etag
            # This is going to sleep for longer than the specified poll_interval, as sending the webhooks to discord already took some time not accounted for.
            # For our purposes this doesn't really matter
            print('Sleeping for', pollresponse.poll_interval)
            time.sleep(pollresponse.poll_interval)
        except Exception as e:
            print(e)
            time.sleep(config.POLL_INTERVAL)

