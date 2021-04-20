from config import token, base
from commands import Command
import util, threading, time, objects, asyncio
import Queue

offset = 0
blacklist = objects.SharedSet(set(open('blacklist.txt', 'r').read().splitlines()))
cmd = Command(blacklist)

def updates():
    global offset
    while True:
        res = util.updates(util.updates_url + (offset>0)*f'?timeout=100&offset={offset}') #only appends full string if offset is already set greater than 0
        if res['ok'] and len(res['result']) > 0:
            offset = res['result'][-1]['update_id']+1
            yield res
        else:
            time.sleep(0.5)
        

def main():
    global offset
    res = util.updates(util.updates_url) #results go unserved after restart. users must recall commands. broadcast?
    print(res)
    if res['ok']:
        if len(res['result']) > 0:
            offset = res['result'][-1]['update_id']+1
    else:
        print('error in receive.main()')
        print(str(res))
    
    for res in updates():
        for result in res['result']:
            uid = result['message']['from']['username']
            if blacklist.has(uid):
                continue
            msg = result['message']['text'].lower()
            chatid = result['message']['chat']['id']
            parse = cmd.parse(msg, uid, chatid)
            if not parse[0]:
                util.send(chatid, parse[1])
                continue
            action = cmd.serve()
            if not action[0]: #has error
                util.send(chatid, action[1])
                #asynchronous log/backup if has 3rd element
                if len(action) == 3: #has error and message, log
                    util.log('Err', action[2])
##                        if action[1].startswith('Error'):
##                            print(action[2])
##                        util.send(chatid, action[1])
                continue
            if len(action) > 1: #send extra message if there is one
                util.send(chatid, action[1])
main()
