import datetime
from typing import Union, Callable, Any, Mapping, Optional

import os

from time import sleep

from dateutil.parser import parse

from googleapiclient import discovery

from slacker import Slacker

CALENDAR_APP_KEY = os.environ.get("EVENT_POLL_GOOGLE_APP_KEY", None)
CALENDAR_ID = os.environ.get("EVENT_POLL_CALENDAR_ID", None)

LAST_CHECKED_TO_FILE = os.environ.get("EVENT_POLL_CHECKED_TO_FILE", 'last_checked_to.log')
DATE_FORMAT = os.environ.get("EVENT_POLL_DATE_FORMAT", "%A %B %d, %Y at %I:%M %p")
SLACK_TOKEN = os.environ.get("EVENT_POLL_SLACK_TOKEN", None)
SLACK_CHANNEL_ID = os.environ.get("EVENT_POLL_SLACK_CHANNEL", None)
SLACK_USER_ID = os.environ.get("EVENT_POLL_SLACK_USER", None)
MESSAGE_INTRO = "Will you go to:"


def format_event(event, start_phrase):
    summary = event['summary']
    start = parse(event['start'].get('dateTime', event['start'].get('date'))).strftime(DATE_FORMAT)
    end = parse(event['end'].get('dateTime', event['end'].get('date'))).strftime(DATE_FORMAT)
    location = event.get('location', 'N/A')
    status = event['status']
    description = event.get('description', None)
    result = None
    if status == "confirmed":
        result = f'{start_phrase} {summary} starting on {start} ending on {end} located at {location}'
        if description is not None:
            result += f'\n {description}'
    return result


def message_is_poll(message: Mapping[str, Any]) -> bool:
    return message.get('username', '') == 'Simple Poll v2'


def clear_messages_since(channel: str, ts: Union[datetime.datetime, int, str, float],
                         slack_client: Slacker, filter_: Callable[[Mapping[str, Any]], bool] = lambda _: True) -> None:
    if isinstance(ts, datetime.datetime):
        slack_ts = ts.timestamp()
    else:
        slack_ts = float(ts)
    history_response = slack_client.channels.history(channel, oldest=slack_ts, count=500)
    messages = history_response.body['messages']
    for message in filter(filter_, messages):
        slack_client.chat.delete(channel, message['ts'])
        sleep(3)


class MessageSummary:
    def __init__(self, title, start, end, location):
        self.title = title
        self.start = start
        self.end = end
        self.location = location


def message_is_poll_for_question(message: Mapping[str, Any], question: str) -> bool:
    return message_is_poll(message) and message.get('text', '').startswith(f'*{question}*')


def message_is_poll_for_event(message: Mapping[str, Any]) -> Optional[MessageSummary]:
    if message_is_poll(message) and message.get("text", "").startswith(f"*{MESSAGE_INTRO}"):
        question = message.get("text").split("*")[1]
        title = question.split(f'{MESSAGE_INTRO} ')[1].split(" starting on ")[0]
        start = question.split(f' starting on ')[1].split(" ending on ")[0]
        end = question.split(' ending on ')[1].split(' located at ')[0]
        location = question.split(' located at ')[1]
        return MessageSummary(title, start, end, location)
    else:
        return None


def main() -> None:
    last_checked_to = None
    if os.path.exists(LAST_CHECKED_TO_FILE):
        with open(LAST_CHECKED_TO_FILE) as lc:
            last_checked_to = lc.read().strip()

    now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    one_week = (datetime.datetime.utcnow() + datetime.timedelta(days=5)).isoformat() + 'Z'

    print('----------------------------------------')
    print('Running at {}'.format(datetime.datetime.now().strftime(DATE_FORMAT)))

    service = discovery.build('calendar', 'v3', developerKey=CALENDAR_APP_KEY)
    if last_checked_to is None:
        last_checked_to = now

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=last_checked_to, timeMax=one_week, singleEvents=True,
        orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        print('No new upcoming events found.')

    slack_client = Slacker(SLACK_TOKEN)

    for event in events:
        start = parse(event['start']['dateTime'])
        if start < parse(last_checked_to):
            continue
        posted_event = format_event(event, MESSAGE_INTRO)
        if posted_event is not None:
            posted_event = posted_event.replace('"', "'")
            event_command = f'"{posted_event}" "Yes" "No" "Unsure" "Late(Please comment by how much)"'
            print(event_command)
            slack_client.chat.command(channel=SLACK_CHANNEL_ID, command="/poll2",
                                      text=event_command)
            sleep(5)
            history = slack_client.channels.history(channel=SLACK_CHANNEL_ID, count=10, unreads=False)
            messages = history.body.get('messages', [])
            for message in messages:
                print(message)
                if message_is_poll_for_question(message, posted_event):
                    if message.get('subtype', 'pinned_item') != 'pinned_item' and \
                            'pinned_to' not in message:
                        print('Pinning message at timestamp:', message['ts'])
                        slack_client.pins.add(channel=SLACK_CHANNEL_ID, timestamp=message['ts'])
                    else:
                        if message.get('text', '') == f'/poll2 {event_command}' and \
                                message.get('user', '') == SLACK_USER_ID:
                            print('Deleting at timestamp:', message['ts'])
                            # slack_client.chat.delete(channel=SLACK_CHANNEL_ID, ts=message['ts'])

    pin_response = slack_client.pins.list(channel=SLACK_CHANNEL_ID)
    pins = pin_response.body.get('items', [])
    for pin in pins:
        message = pin.get('message', {})
        message_summary = message_is_poll_for_event(message)
        if message_summary is not None:
            end_time = datetime.datetime.strptime(message_summary.end, DATE_FORMAT)
            if end_time < datetime.datetime.now():
                print('Unpinning message:', message_summary.title, message)
                slack_client.pins.remove(channel=SLACK_CHANNEL_ID, timestamp=message['ts'])

    last_checked_to = one_week
    with open(LAST_CHECKED_TO_FILE, 'w') as lm_write:
        lm_write.write(last_checked_to)


if __name__ == '__main__':
    main()
    if os.path.exists('runlog.log'):
        if os.path.getsize('runlog.log') > 20 * 2**10:
            os.rename('runlog.log', 'runlog-{}.log'.format(datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")))
