#!/usr/bin/python

import datetime
import collections
import json
import sys
import threading
import time
import pprint
import fuzzywuzzy.process
import fuzzywuzzy.fuzz
import string
import random
import os

from Queue import Queue, Empty
from slackclient import SlackClient


OWNER_USER = '' # Needs to be a flag
BOT_TOKEN = '' # Needs to be a flag
BOT_USER = '' # Needs to be a flag
BOT_NAME = 'Hearthstone Bot'
OWNER_CHAN = None
QUIT = Queue()

def slackbot():
    global QUIT

    sc = SlackClient(BOT_TOKEN)
    if not sc.rtm_connect():
        print "Connection failed! Abort!"
        return 1

    def send_error(msg):
        global OWNER_CHAN
        if OWNER_CHAN is None:
            resp = json.loads(sc.api_call('im.open', user=OWNER_USER))
            if not resp['ok']:
                raise Exception("Shit happened: %s" % (msg, ))
            OWNER_CHAN = resp['channel']['id']
        sc.api_call('chat.postMessage', channel=OWNER_CHAN,
                    username=BOT_NAME, text=msg)
    send_error('Spinning up!')

    def reply(msg, reply):
        sc.api_call('chat.postMessage', channel=msg['channel'],
                    username=BOT_NAME, text=reply)

    def debug_reply(msg, reply):
        chan = chans.get(msg['channel'], msg['channel'])
        sc.api_call('chat.postMessage', channel=OWNER_CHAN,
                    username=BOT_NAME, text='I would have written this into %s: %s' %(chan, reply))

    def isHighPriority(msg):
        if msg['channel'].startswith('D'):
            return True
        return '<@%s>' % BOT_USER in msg['text']


    def status(msg):
        reply(msg, 'Hello there, %s!' % (user['profile']['email'], ))

    def help(msg):
        reply(msg, "I understand these commands: %s" % COMMANDS.keys())

    def quit(msg):
        reply(msg, "Getting dizzy...")
        QUIT.put(True)

    def randomCard(msg):
        card = random.choice(Config.cards_by_id.values())
        reply(msg, formatCardForReply(card, sendDebug=False, debugText=""))

    COMMANDS = {
        'help': help,
        'quit': quit,
        'status': status,
        'random': randomCard,
    }

    LAST_SENT = collections.defaultdict(lambda: datetime.datetime(year=1970, month=1, day=1))
    RESEND_INTERVAL = datetime.timedelta(hours=1)

    chans, users = {}, {}
    LOW_PRI_MIN_PROCESS_SCORE = 50
    LOW_PRI_MIN_PRATIO_SCORE = 90
    LOW_PRI_MIN_RATIO_SCORE = 50
    while True:
        # Now handle incoming messages
        msgs = sc.rtm_read()
        if not msgs:
            time.sleep(1)
            continue
        for msg in msgs:
            if msg['type'] != 'message' or msg.get('subtype', None) == 'bot_message':
                continue
            if 'text' not in msg:
                continue

            print "<< %s" % (msg, )
            if msg['channel'] not in chans:
                if msg['channel'].startswith('C'):
                    chan = json.loads(sc.api_call('channels.info', channel=msg['channel']))
                    if not chan['ok']:
                        send_error("Failed to get information on channel %s: %s" % (msg['channel'], chan))
                        continue
                    chans[msg['channel']] = chan['channel']
                elif msg['channel'].startswith('D'):
                    all_ims = json.loads(sc.api_call('im.list'))
                    if not all_ims['ok']:
                        send_error("Failed to get information on IMs %s: %s" % (msg['channel'], all_ims))
                        continue
                    for im in all_ims['ims']:
                        if im['id'] == msg['channel']:
                            chans[msg['channel']] = im
                            break
                    else:
                        send_error("Failed to get info on IM %s: %s" % (msg['channel'], all_ims))
            # Got a message on a channel, now let's do something
            if msg['user'] not in users:
                user = json.loads(sc.api_call('users.info', user=msg['user']))
                if not user['ok']:
                    send_error("Failed to get information on user %s: %s" % (msg['user'], user))
                    continue
                users[msg['user']] = user['user']
            user = users[msg['user']]

            if isHighPriority(msg):
                print 'High priority message!'
                text = removeAtMentions(msg['text']).strip()
                print 'Without at mentions I got %s' % text
                if text.startswith('!'):
                    print 'Got a command.'
                    command = text[1:].split()
                    if command:
                        COMMANDS.get(command[0], help)(msg)
                    continue

                text = normalizeUserInput(text)
                for cards, process_score, pratio_score, ratio_score in getCardByFuzzyName(text, min_process_score=30, min_pratio_score=30, min_ratio_score=0):
                    sorry_text = ''
                    if (process_score < LOW_PRI_MIN_PROCESS_SCORE or
                        pratio_score < LOW_PRI_MIN_PRATIO_SCORE or
                        ratio_score < LOW_PRI_MIN_RATIO_SCORE):
                        sorry_text = "This is a bad match, but since you @ mentioned me I'll try anyway: "
                    for card in cards:
                        reply(msg, sorry_text + formatCardForReply(card, sendDebug=False, debugText="%s,%s,%s" %(process_score, pratio_score, ratio_score)))
            else:
                print 'Low priority message...'
                text = normalizeUserInput(msg['text'])
                now = datetime.datetime.now()
                for cards, process_score, pratio_score, ratio_score in getCardByFuzzyName(text, min_process_score=LOW_PRI_MIN_PROCESS_SCORE, min_pratio_score=LOW_PRI_MIN_PRATIO_SCORE, min_ratio_score=LOW_PRI_MIN_RATIO_SCORE):
                    for card in cards:
                        last_sent = LAST_SENT[card['id']]
                        if now - last_sent > RESEND_INTERVAL:
                            LAST_SENT[card['id']] = now
                            reply(msg, formatCardForReply(card, sendDebug=False, debugText=""))
                        else:
                            print 'Not replying about %s because I just did at %s.' % (card['name'], last_sent)


class Config:
    cards_by_id = {}
    cards_by_name = collections.defaultdict(list)

def formatCardForReply(card, debugText, sendDebug):
    result = 'http://wow.zamimg.com/images/hearthstone/cards/enus/animated/%s_premium.gif' % card['id']
    if debugText and sendDebug:
        result += '\nDEBUG INFO:' + debugText
    return result

def getCards(name=None):
    result = []
    if name:
        for id in Config.cards_by_name[name]:
            result.append(Config.cards_by_id[id])
    return result

def getCardByFuzzyName(name, min_process_score=0, min_pratio_score=0, min_ratio_score=0, limit=1):
    if not name.strip():
        return
    results = fuzzywuzzy.process.extract(name, Config.cards_by_name.keys(), limit=limit + 10)
    for best_name, process_score in results:
        if process_score < min_process_score:
            print "Not matching %s to %s due to process_score %s < %s" % (best_name, name, process_score, min_process_score)
            continue
        best_cards = getCards(best_name)
        pratio_score = fuzzywuzzy.fuzz.partial_ratio(name, best_name)
        if pratio_score < min_pratio_score:
            print "Not matching %s to %s due to pratio_score %s < %s" % (best_name, name, pratio_score, min_pratio_score)
            continue
        ratio_score = fuzzywuzzy.fuzz.ratio(name, best_name)
        if len(name) > 10 or ' ' in name:
            print "Not considering ratio_score test due to long input: %s" % name
        elif ratio_score < min_ratio_score:
            print "Not matching %s to %s due to ratio_score %s < %s" % (best_name, name, ratio_score, min_ratio_score)
            continue
        yield best_cards, process_score, pratio_score, ratio_score
        limit -= 1
        if limit <= 0:
            return

def normalizedNames(name):
    name = name.lower()
    if " the" in name:
        for result in normalizedNames(name[0:name.find(" the")]):
            yield result
    yield name
    yield removePunctuation(name)
    yield removePunctuationSpaces(name)


def normalizeUserInput(otext):
    text = otext.lower()
    text = removeEmoji(text)
    text = removePunctuation(text)
    text = removeStopWords(text)
    print 'Normalized user input:\n%s\n  =>\n%s' % (otext, text)
    return text

STOP_WORDS = [u'i', u'me', u'my', u'myself', u'we', u'our', u'ours', u'ourselves',
u'you', u'your', u'yours', u'yourself', u'yourselves', u'he', u'him', u'his',
u'himself', u'she', u'her', u'hers', u'herself', u'it', u'its', u'itself',
u'they', u'them', u'their', u'theirs', u'themselves', u'what', u'which', u'who',
u'whom', u'this', u'that', u'these', u'those', u'am', u'is', u'are', u'was',
u'were', u'be', u'been', u'being', u'have', u'has', u'had', u'having', u'do',
u'does', u'did', u'doing', u'a', u'an', u'the', u'and', u'but', u'if', u'or',
u'because', u'as', u'until', u'while', u'of', u'at', u'by', u'for', u'with',
u'about', u'against', u'between', u'into', u'through', u'during', u'before',
u'after', u'above', u'below', u'to', u'from', u'up', u'down', u'in', u'out',
u'on', u'off', u'over', u'under', u'again', u'further', u'then', u'once',
u'here', u'there', u'when', u'where', u'why', u'how', u'all', u'any', u'both',
u'each', u'few', u'more', u'most', u'other', u'some', u'such', u'no', u'nor',
u'not', u'only', u'own', u'same', u'so', u'than', u'too', u'very', u's', u't',
u'can', u'will', u'just', u'don', u'should', u'now']
def removeStopWords(s):
    return ' '.join([w for w in s.split() if w not in STOP_WORDS])

def removeAtMentions(s):
    if '<@' not in s or '>' not in s:
        return s
    if '>:' in s:
        return removeAtMentions(s[:s.find('<@')] + s[s.find('>:') + 2:])
    return removeAtMentions(s[:s.find('<@')] + s[s.find('>') + 1:])

def removeEmoji(s):
    result = []
    for token in s.split():
        if token.startswith(':') and token.endswith(':'):
            continue
        result.append(token)
    return ' '.join(result)

TRANSLATE_TABLE = {ord(c):None for c in string.punctuation}
def removePunctuation(s):
    if not s:
        return ''
    return s.translate(TRANSLATE_TABLE)

TRANSLATE_TABLE_SPACES = {ord(c):ord(' ') for c in string.punctuation}
def removePunctuationSpaces(s):
    if not s:
        return ''
    return s.translate(TRANSLATE_TABLE_SPACES)

def loadConfig():
    RELEVANT_CARD_SETS = ["Classic", "Curse of Naxxramas", "Blackrock Mountain", "Basic", "Promotion", "Reward", "Goblins vs Gnomes", "Tavern Brawl"]
    RELEVANT_CARD_TYPES = ['Minion', 'Spell', 'Weapon']
    IRRELEVANT_ID_PREFIXES = ['NAX', 'BRMA', 'XXX', 'CRED', 'BRMC']

    # d == {set_name: [card_dict]}
    # card_dict == {k:v for k in ['attack', 'collectible', 'cost', 'elite', 'health', 'id', 'name', 'race', 'rarity', 'text', 'type']}
    # type == ['Minion', 'Hero Power', ...]
    #
    # cards_by_id: {id: card_Dict}
    # cards_by_name: {name: id}
    with open('AllSets.json') as f:
        d = json.load(f)
        RELEVANT_CARD_SETS = d.keys()
        for s in RELEVANT_CARD_SETS:
            for c in d[s]:
                if c['type'] not in RELEVANT_CARD_TYPES:
                    continue
                if any([c['id'].startswith(prefix) for prefix in IRRELEVANT_ID_PREFIXES]):
                    continue
                Config.cards_by_id[c['id']] = c
                names = set(normalizedNames(c['name']))
                if len(names) > 1:
                    pprint.pprint(names)
                for name in names:
                    Config.cards_by_name[name].append(c['id'])


def main(args):
        slackbot_thread = threading.Thread(target=slackbot)
        slackbot_thread.daemon = True
        slackbot_thread.start()
        loadConfig()

        while True:
            try:
                QUIT.get(True, 0.1)
                print "Exiting.."
                return 0
            except Empty:
                continue

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
