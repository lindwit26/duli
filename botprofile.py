from commands import Command
import util, threading, time, objects, asyncio, aiohttp, string, ujson, math, sys, traceback, os, pprint
import bitcoinlib, json
from bitcoinlib.wallets import Wallet
from queue import Queue
from objects import SharedSet, SharedDict, SharedList
from multiprocessing import Process

class BotProfile():
    def __init__(self, profile):
        s = time.time()
        self.lock = threading.Lock()
        self.offset = 0
        self.refresh(profile, False)
        self.blacklist = SharedDict(dict(map((lambda ids : (ids[0], int(float(ids[1])))), [i.split('|') for i in self.readf('blacklist.txt').read().splitlines()])))
        self.settings = ujson.loads(self.readf('persistent.json').read())
        self.temp_ratelimit = {}
        self.limited = SharedDict({})
        self.tab = tab = str.maketrans(string.ascii_lowercase + string.ascii_uppercase,string.ascii_lowercase * 2) #for str.translate() - fast str to lowercase
        self.q = Queue()
        self.broadcastq = Queue()
        util.fetch_BTC_price(self.btc_proxy)
        self.futures = SharedDict({}) #{txid: task} for async processing delete tasks. cancel tasks when order is verified
        self.futures_str = SharedDict(dict(map((lambda ftr : (ftr[0], '|'.join(ftr))), [l.split('|') for l in self.readf('futures.txt').read().splitlines() if l])))
        self.cmd = Command(self)
        self.btc = util.BTC(self)
        self.utxos_thread = threading.Thread(target=self.btc.refresh_utxos) #for /balance and withdraw. is referenced in command in serve but can't be served until init is done
        self.utxos_thread.daemon = True
        self.utxos_thread.start()
        self.updates_thread = threading.Thread(target=self.updates_refresh) #product_updates queue listener
        self.updates_thread.daemon = True
        self.updates_thread.start()
        print(self.futures_str.__iter__())
        for future in self.futures_str.values():
            future = future.split('|')
            #t = (int(future[2])+int(future[3]))-int(time.time())
            if self.cmd.pending_orders.has(future[0]):
                t = int(future[2]) - int(time.time())
                if future[1] == 'delpending': #unnecessary
                    del_task = asyncio.ensure_future(self.cmd.delete_pending_in_future(future[0], future[3], t))
                    self.futures.add(future[0], del_task)
                    del_task2 = asyncio.ensure_future(self.btc.future_remove_received_addrs(future[3], t))
                    self.futures.add(future[3], del_task2)
    ##            elif future[1] == 'deladdrs':
    ##                del_task2 = asyncio.ensure_future(self.btc.future_remove_received_addrs(future[0], t))
    ##                self.futures.add(future[0], del_task2)
            else:
                self.futures_str.remove(future[0])
        self.backup_futures()
        self.send = util.Send(self.q, self.broadcastq, self.message_url, self.tg_proxy)
        self.utxos_last_updated = 0
        print(self.name + f' - finished init ({time.time()-s}s)')
        #super(BotProfile, self).__init__()

    def refresh(self, profile, with_classes):
        self.lock.acquire()
        try:
            print('refreshing ' + profile['profile'])
            print(profile)
            if 'work_dir' in profile:
                self.wd = profile['work_dir'] + '//'*(not profile['work_dir'].endswith('//'))
                if not os.path.exists(profile['work_dir']):
                    os.mkdir(profile['work_dir'])
            self.log = util.Log(self.wd)
            self.name = profile['profile']
            self.bot_name = profile['bot-name']
            self.bot_token = profile['bot-token']
            self.tg_proxy = profile['telegram_proxy']
            self.btc_proxy = profile['btc_proxy']
            self.base_url = 'https://api.telegram.org/bot' + self.bot_token
            self.message_url = f'{self.base_url}/sendMessage'
            self.updates_url = f'{self.base_url}/getUpdates'
            self.wallet_name = profile['bitcoinlib_HDWallet_name']
            self.wallet = Wallet(self.wallet_name) if bitcoinlib.wallets.wallet_exists(self.wallet_name) else Wallet.create(self.wallet_name)
            self.max_orders = profile['maximum_monthly_order_count']
            self.package = profile['package']
            self.kill = profile['kill']
            if with_classes:
                self.send.refresh(self.tg_proxy)
                self.btc.refresh(self.tg_proxy)
                self.cmd.refresh(self.max_orders)
        except Exception as e:
            print('Exception refreshing profile: ' + str(e))
        finally:
            self.lock.release()

    def updates_refresh(self):
        last_checked = os.stat(os.path.join(self.wd, 'config.json')).st_mtime
        while True:
            if os.stat(os.path.join(self.wd, 'config.json')).st_mtime != last_checked:
                last_checked = os.stat(os.path.join(self.wd, 'config.json')).st_mtime
                self.refresh(ujson.loads(self.readf('config.json').read()), True)
##            while not self.product_updates.empty():
##                try:
##                    profile = self.product_updates.get()
##                    self.refresh(profile, True)
##                except Exception as e:
##                    print(f'Err refreshing in {self.name} updates_refresh(): {e}')
            time.sleep(5)

    def readf(self, file_name):
        if not os.path.exists(self.wd + file_name):
            open(self.wd + file_name, 'w')
        return open(self.wd + file_name, 'r')

    def backup_futures(self):
        self.log.backup('futures.txt', 'w', '\n'.join(self.futures_str.copy().values()))

##    def future_del(self, future):
##        t = (int(future[2])+int(future[3]))-int(time.time())
##        print(f'future deleting {future[0]} in {t} seconds')
##        if future[1] == 'delpending':
##            self.cmd.delete_pending_in_future(future[0], future[4], t)
##        elif future[1] == 'deladdrs':
##            self.btc.future_remove_received_addrs(future[0], t)
##        self.futures.remove(future[0])
##        self.log.backup('futures.txt', 'w', '\n'.join())
    
    def pause(self):
        #pause profile thread/process for maintenance
        pass

    def create_transaction(self, address, fee, chatid):
        try:
            #tx = self.wallet.transaction_create((address, amount), fee=fee) #fee is subtracted from amount if needed so not necessary to check size before sending
            if self.utxos_thread.is_alive():
                self.add_msg(chatid, 'Refreshing wallet UTXOs and network fee. This usually takes a few minutes')
                self.utxos_thread.join()
            elif self.btc.newest_tx_ts >= self.btc.utxos_last_updated and time.time() - self.btc.utxos_last_updated >= 3600:
                self.add_msg(chatid, 'Refreshing wallet UTXOs and network fee. This usually takes a few minutes')
                self.btc.refresh_utxos()
            self.add_msg(chatid, 'Attempting to create transaction')
            if not fee:
                fee = self.btc.current_fees
            fee *= 1024 #fee per kb
##            if amount  == 'all':
##                #use wallet.sweep()
##                self.wallet._balance_update()
##                amount = self.wallet.balance()
##                self.settings['btc_wallet_balance'] = self.wallet.balance()
##                self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))
##            else:
##                amount = int(float(amount)*100000000)
##            #tx = self.wallet.send_to(address, amount, fee=fee)
##            tx = self.wallet.transaction_create([(address, amount)], fee=fee)
##            pprint(vars(tx))
            self.cmd.set_btc_bal(0)
            tx = self.wallet.sweep(address, fee_per_kb=fee) #min_confirms=1 required 2 for some reason. maybe just on ubuntu and that version
            pprint.pprint(vars(tx))
            self.log.backup('withdraw_transactions.txt', 'a', pprint.pformat(vars(tx)))
            self.add_msg(chatid, f'Successfully created transaction: {tx.txid}')
            if not self.utxos_thread.is_alive():
                self.utxos_thread = threading.Thread(target=self.btc.refresh_utxos)
                self.utxos_thread.daemon = True
                self.utxos_thread.start()
        except:
            ex_type, ex_value, ex_traceback = sys.exc_info()
            trace_back = traceback.extract_tb(ex_traceback)
            trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
            self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
            self.add_msg(chatid, f'Something went wrong creating your BTC transaction. Try again in a few minutes: {ex_value}')
    
    async def get_updates(self, session, url):
        try:
            async with session.get(url, proxy=self.tg_proxy) as res:
                return await res.json()
        except:
            ex_type, ex_value, ex_traceback = sys.exc_info()
            trace_back = traceback.extract_tb(ex_traceback)
            trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
            self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
            return None

    async def updates(self):
        async with aiohttp.ClientSession() as session:
            while not self.kill:
                res = await self.get_updates(session, self.updates_url + (self.offset>0)*f'?timeout=100&offset={self.offset}') #only appends full string if offset is already set greater than 0
                if res and res['ok'] and len(res['result']) > 0:
                    self.offset = res['result'][-1]['update_id']+1
                    yield res
                else:
                    await asyncio.sleep(0.1)
            print(self.name + ' killed')
            sys.exit()

    def add_msg(self, chatid, msg):
        send_ar = util.clean_msg(msg)
        for m in send_ar:
            self.q.put([chatid, m])

    async def main(self):
        msg_thread = threading.Thread(target=self.send.start_handle)
        msg_thread.daemon = True
        msg_thread.start()
        btc_thread = threading.Thread(target=self.btc.start_handle, args=(self.futures, ))
        btc_thread.daemon = True
        btc_thread.start()
##        utxos_thread = threading.Thread(target=self.btc.refresh_utxos)
##        utxos_thread.daemon = True
##        utxos_thread.start()
##        try:
        start = math.floor(time.time())
        async with aiohttp.ClientSession() as session:
            res = await self.get_updates(session, self.updates_url)
            print(res)
        while (res['ok'] and len(res['result']) > 0):
            if res['result'][-1]['message']['date'] < start:
                self.offset = res['result'][-1]['update_id']+1 #skip commands sent while down
            else:
                break
            async with aiohttp.ClientSession() as session:
                res = await self.get_updates(session, self.updates_url)
            await asyncio.sleep(0.02)
        for result in res['result']:
            if result['message']['date'] >= start:
                self.offset = result['update_id']
                break
        async for res in self.updates():
            for result in res['result']:
                try: #could be edited message
                    uid = result['message']['from']['username'].translate(self.tab) #to lowercase
                except:
                    continue
                if self.blacklist.has(uid):
                    t = self.blacklist.get(uid)
                    if 0 == t or t > time.time(): #if perm ban or still has ban time
                        continue
                    else: #ban time passed, remove
                        self.blacklist.remove(uid)
                        #worth just leaving in file
                chatid = result['message']['chat']['id']
                if uid in self.temp_ratelimit and (time.time() - self.temp_ratelimit[uid]) < 0.5: #less than half a second has passed
                    self.add_msg(chatid, 'Please slow down. You may try again shortly')
                    done = time.time()+30
                    self.blacklist.add(uid, done) #temp ban for 30 sec
                    self.log.backup('blacklist.txt', 'a', f'{uid}|{done}')
                    #not necessary to set current time in temp_ratelimit
                    continue
                self.temp_ratelimit[uid] = time.time()
                try: #could be file
                    msg = result['message']['text']
                except:
                    continue
                parse = await self.cmd.parse(msg, uid, chatid)
                if not parse[0]:
##                    if parse[1] == 'refresh_utxos': #probably unnecessary. could add to create_transaction
##                        if utxos_thread.is_alive():
##                            self.add_msg(chatid, 'Still updating wallet UTXOs')
##                            continue
##                        #if btc has new transaction since self.utxos_last_updated and has been over 1 hour
##                        if self.btc.newest_tx_ts > self.btc.utxos_last_updated and time.time() - self.btc.utxos_last_updated >= 3600:
##                            self.add_msg(chatid, parse[2])
##                            utxos_thread = threading.Thread(target=self.btc.refresh_utxos)
##                            utxos_thread.daemon = True
##                            utxos_thread.start()
##                            continue
##                        #if here, thread is finished and most recently updated, can serve
##                    else:
                    self.add_msg(chatid, parse[1])
                    continue
                action = await self.cmd.serve()
                if len(action) > 1:
                    if action[0] and action[1] == 'create_transaction':
                        #self.add_msg(chatid, 'Attempting to create transaction')
                        args = action[2]+(chatid, )
                        tx_thread = threading.Thread(target=self.create_transaction, args=args)
                        tx_thread.daemon = True
                        tx_thread.start()
                    else:
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
##        except KeyboardInterrupt as e:
##            msg_thread.stop()
##            btc_thread.stop()
##            return

def run(bp):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bp.main())
    loop.close()

##def run(j, q): #for multiprocessing
##    bp = BotProfile(j, q)
##    loop = asyncio.get_event_loop()
##    loop.run_until_complete(bp.main())
##    loop.close()

##j = {
##    "profile": "test",
##    "bot-name": "order_help_bot",
##    "bot-token": "1385262841:AAGbufdY0ojcljJwritFO5BSoeFfOzZ2LOQ",
##    "bitcoinlib_HDWallet_name": "test",
##    "work_dir": ".",
##    "package": "bronze",
##    "maximum_monthly_order_count": 1000,
##    'telegram_proxy': '', #'http://23.108.46.130:12345',
##    'btc_proxy': '', #'http://23.108.46.130:12345', #can be single proxy, multiple separated by commas, or file
##    
##}
##
if len(sys.argv) != 2:
    print('python botprofile.py WORK_DIR')
    sys.exit()
j = ujson.loads(open(os.path.join(sys.argv[1], 'config.json'), 'r').read())
j['work_dir'] = sys.argv[1]
p = BotProfile(j)
run(p)
