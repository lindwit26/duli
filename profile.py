from commands import Command
import util, threading, time, objects, asyncio, aiohttp, string
import bitcoinlib, json
from bitcoinlib.wallets import Wallet
from queue import Queue
from objects import SharedSet, SharedDict, SharedList

class Profile:
    def __init__(self, profile):
        self.offset = 0
        self.wd = profile['work_dir'] + '/'*(not profile['work_dir'].endswith('/'))
        self.log = util.Log(self.wd)
        self.name = profile['profile']
        self.bot_name = profile['bot-name']
        self.bot_token = profile['bot-token']
        self.base_url = 'https://api.telegram.org/bot' + self.bot_token
        self.message_url = f'{self.base_url}/sendMessage'
        self.updates_url = f'{self.base_url}/getUpdates'
        self.wallet_name = profile['bitcoinlib_HDWallet_name']
        self.wallet = Wallet(self.wallet_name) if bitcoinlib.wallets.wallet_exists(self.wallet_name) else Wallet.create(self.wallet_name)
        self.max_orders = profile['maximum_monthly_order_count']
        self.package = profile['package']
        self.blacklist = SharedDict(dict(map((lambda ids : (ids[0], int(ids[1]))), [i.split('|') for i in open(self.wd + 'blacklist.txt', 'r').read().splitlines()])))
        self.temp_ratelimit = {}
        self.limited = SharedDict({})
        self.tab = tab = str.maketrans(string.ascii_lowercase + string.ascii_uppercase,string.ascii_lowercase * 2) #for str.translate() - fast str to lowercase
        self.q = Queue()
        self.broadcastq = Queue()
        self.futures = SharedDict({}) #{txid: task} for async processing delete tasks. cancel tasks when order is verified
        self.cmd = Command(self)

    def pause():
        #pause profile thread/process for maintenance
        pass

    async def get_updates(self, session, url):
        async with session.get(url) as res:
            return await res.json()

    async def updates(self):
        async with aiohttp.ClientSession() as session:
            while True:
                res = await self.get_updates(session, self.updates_url + (self.offset>0)*f'?timeout=100&offset={self.offset}') #only appends full string if offset is already set greater than 0
                if res['ok'] and len(res['result']) > 0:
                    self.offset = res['result'][-1]['update_id']+1
                    yield res
                else:
                    await asyncio.sleep(0.5)

    def add_msg(self, chatid, msg):
        send_ar = util.clean_msg(msg)
        for m in send_ar:
            self.q.put([chatid, m])

    async def main(self):
        self.send = util.Send(self.q, self.broadcastq)
        msg_thread = threading.Thread(target=self.send.start_handle)
        msg_thread.start()
        self.btc = util.BTC(self.cmd, self.q, self.futures, self.log)
        btc_thread = threading.Thread(target=self.btc.start_handle)
        btc_thread.start()
        async with aiohttp.ClientSession() as session:
            res = await self.get_updates(session, self.updates_url + (self.offset>0)*f'?timeout=100&offset={self.offset}')
        if res['ok'] and len(res['result']) > 0:
            self.offset = res['result'][-1]['update_id']+1 #skip commands sent while down
        async for res in self.updates():
            for result in res['result']:
                uid = result['message']['from']['username'].translate(self.tab) #to lowercase
                if self.blacklist.has(uid):
                    t = self.blacklist.get(uid)
                    if 0 == t or t > time.time(): #if perm ban or still has ban time
                        continue
                    else: #ban time passed, remove
                        self.blacklist.remove(uid)
                chatid = result['message']['chat']['id']
                if uid in self.temp_ratelimit and (time.time() - self.temp_ratelimit[uid]) < 0.5: #less than half a second has passed
                    self.add_msg(chatid, 'Please slow down. You may try again shortly')
                    self.blacklist.add(uid, time.time()+30) #temp ban for 30 sec
                    #not necessary to set current time in temp_ratelimit
                    continue
                self.temp_ratelimit[uid] = time.time()
                msg = result['message']['text'].translate(self.tab)
                parse = await self.cmd.parse(msg, uid, chatid)
                if not parse[0]:
                    self.add_msg(chatid, parse[1])
                    continue
                action = await self.cmd.serve()
                if len(action) > 1:
                    self.add_msg(chatid, action[1])
                    if not action[0]:
                        #asynchronous log/backup if has 3rd element
                        if len(action) == 3: #has error and message, log
                            self.log.log('Err', action[2])
        ##                        if action[1].startswith('Error'):
        ##                            print(action[2])
        ##                        self.add_msg(chatid, action[1])
##                    continue
##                if len(action) > 1: #send extra message if there is one
##                    self.add_msg(chatid, action[1])

def start(profile):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(profile.main())
    loop.close()

j = {
    "profile": "test",
    "bot-name": "order_help_bot",
    "bot-token": "1385262841:AAGbufdY0ojcljJwritFO5BSoeFfOzZ2LOQ",
    "bitcoinlib_HDWallet_name": "test",
    "work_dir": ".",
    "package": "bronze",
    "maximum_monthly_order_count": 1000,
    'telegram_proxy': '',
    'btc_proxies': '', #can be single proxy, multiple separated by commas, or file
    
}

p = Profile(j)
start(p)
