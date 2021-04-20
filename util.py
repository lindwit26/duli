import time, math, aiohttp, asyncio, re, sys, traceback, threading, requests, ujson
from queue import Queue
from aiohttp import ClientSession
from leakybucket import AsyncLimiter
from objects import SharedSet, SharedDict, SharedList
from urllib.parse import quote

last_checked = 0 #last time blockchain.com was queried
btcprice = 0

btc_addr_url = 'https://blockchain.info/rawaddr/'
btc_tx_url = 'https://blockchain.info/rawtx/'
btc_addr_multi_url = 'https://blockchain.info/multiaddr?n=0&active=' #divided by '|'

class Send:

    def __init__(self, q, broadcastq, message_url, proxy):
        self.lock = threading.Lock()
        self.q = q
        self.broadcastq = broadcastq
        #https://stackoverflow.com/questions/45440900/throttling-async-functions-in-python-asyncio
        self.bucket = AsyncLimiter(1, 0.03) #relying on program latency to make up for the remaining .1 seconds
        self.message_url = message_url
        self.stop = True
        self.proxy = proxy

    def refresh(self, proxy):
        with self.lock:
            self.proxy = proxy

    def stop(self):
        self.stop = False

    def add_msg(self, chatid, msg):
        self.q.put([chatid, msg])

    async def send_message(self, session, chatid, msg, n):
        async with self.bucket:
            #async with ClientSession() as session:
            async with session.get(self.message_url + f'?chat_id={chatid}&text={msg}&disable_web_page_preview=true', proxy=self.proxy) as res:
                if res.status == 429:
                    #print('got 429')
                    #print(res)
                    if n < 2:
                        return self.send_message(session, chatid, msg, n+1)
                    else:
                        self.add_msg(chatid, msg)
                        return None
                j = await res.json()
                return j

    async def grab_messages(self):
        async with ClientSession() as session:
            while self.stop:
                tasks = []
                qc = not self.q.empty()
                bc = not self.broadcastq.empty()
                while (qc or bc) and len(tasks) < 60:
                    if bc:
                        b = self.broadcastq.get()
                        tasks.append(asyncio.create_task(self.send_message(session, b[0], b[1], 0)))
                        bc = not self.broadcastq.empty()
                    #can add is str check for flags to print/log
                    if qc:
                        t = self.q.get()
                        tasks.append(asyncio.create_task(self.send_message(session, t[0], t[1], 0)))
                        qc = not self.q.empty()
                if tasks:
                    yield tasks
                    tasks = []
                await asyncio.sleep(0.005)

    async def handle_messages(self):
        async for tasks in self.grab_messages():
            result = await asyncio.gather(*tasks)
            print(result)
        print('done handle')

    def start_handle(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.handle_messages())
        self.loop.close()

def clean_msg(msg): #breaks message at nearest \n char before chunk end. checks chunk_size/2 before end for \n char. strips leading/trailing \n chars
    chunk_size = 4096
    msg = quote(msg.replace('\\n', '\n'))
    if len(msg) <= chunk_size:
        return [msg]
    i = 0
    send_ar = []
    while len(msg) > i+chunk_size:
        look = msg[i+chunk_size-2:i+chunk_size+2].find('%0A') #can only be at index 0, 1
        buff = (2-(look==1))*(look!=-1) #if \n at 0, buff=2, if at 1, buff = 1. if none at all, no buff
        s = msg[i:i+chunk_size-buff]#protect against 2 of 3 chars in '\n' url encoded being last chars in chunk
        f = s.rfind('%0A', 2048)
        jump = chunk_size if f == -1 else f
        app = s[:jump]
        app = re.sub('(^(%0A)+|(%0A)+$)', '', app)
        send_ar.append(app)
        i+=(chunk_size-buff if buff != 0 and f != -1 else (jump+(3-buff)*(look!=-1)))
    send_ar.append(msg[i:])
    return send_ar

def payment_verified(chatid, txid, delivery):
    msg = f'The payment for your order #{txid} was verified. Here is your delivery: {delivery}'

class Log:
    def __init__(self, wd):
        self.lock = threading.Lock()
        self.wd = wd

    def backup(self, file, mode, data):
        if not data and mode == 'a': #if empty and appending, no point
            return
        self.lock.acquire()
        try:
            open(self.wd + file, mode).write(data + '\n'*(not data.endswith('\n')))
        except Exception as e:
            print('error in util.backup(), ' + str(e))
        finally:
            self.lock.release()

    def log(self, type, msg):
        TOLERANCE = 3
        p = float(10**TOLERANCE)
        ts = int(time.time() * p - 0.5)#/p #faster than round()
        self.backup(self.wd + 'log.txt', 'a', f'[{type}] ({ts}) {msg}')

def get_cur_ts():
    return int(time.time()-0.5)

def fiat_to_btc(usd): #currently only supports usd
    return round(usd/btcprice, 8)

#async def get_btc_fee():
#    try:
#        async with ClientSession() as session:
#            async with session.get('https://api.blockchain.info/mempool/fees') as res:
#                if res.status == 200:
#                    j = await res.json()
#                    return j['regular']
#                else:
#                    return None
#    except:
#        ex_type, ex_value, ex_traceback = sys.exc_info()
#        trace_back = traceback.extract_tb(ex_traceback)
#        trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
#        self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
#        return None

def fetch_BTC_price(proxy):
    global last_checked
    global btcprice
    ts = get_cur_ts()
    try:
        with requests.get(f'https://www.blockchain.com/prices/api/coin-historical-price?fsym=BTC&tsyms=USD&ts={ts}&limit=1', proxies={'http': proxy, 'https': proxy}) as res:
            last_checked = get_cur_ts()
            j = res.json()
            btcprice = j['BTC']['USD']
    except:
        pass

async def get_BTC_price(proxy):
    global last_checked
    global btcprice
    async with ClientSession() as session:
        while True:
            ts = get_cur_ts()
            try:
                async with session.get(f'https://www.blockchain.com/prices/api/coin-historical-price?fsym=BTC&tsyms=USD&ts={ts}&limit=1', proxy=proxy) as res:
                    last_checked = get_cur_ts()
                    j = await res.json()
                    btcprice = j['BTC']['USD']
            except Exception as e:
                print(e)
            await asyncio.sleep(10)

class BTC:

    max_btc_wait = 10 #10 seconds
    max_cycle_time = 300 #5 min
    cycle_btc_div = max_cycle_time/max_btc_wait
    chunk_size = 100

    def __init__(self, profile):
        self.lock = threading.Lock()
        self.profile = profile
        self.cmd = profile.cmd
        self.wallet = profile.wallet
        self.pendingBTC = self.cmd.pendingBTC
        self.q = profile.q
        self.bcbucket = AsyncLimiter(1, 10) #blockchain.com rate limit: 1 every 10 sec
        self.bitapsbucket = AsyncLimiter(1, 0.34) #bitaps rate limit: 15/5 sec
        self.futures = SharedDict({})
        self.received_addrs = set() #only added to when address has received >= amount expected
        self.log = profile.log
        self.stop = False
        self.proxy = profile.btc_proxy
        self.newest_tx_ts = 0
        self.utxos_last_updated = 0
        self.current_fees = self.get_btc_fee()

    def refresh(self, proxy):
        with self.lock:
            self.proxy = proxy

    def stop(self):
        self.stop = True

    def get_btc_fee(self):
        try:
            res = requests.get('https://api.blockchain.info/mempool/fees', proxies={'http':self.proxy, 'https':self.proxy})
            if res.status_code == 200:
                return res.json()['regular']
            return None
        except:
            ex_type, ex_value, ex_traceback = sys.exc_info()
            trace_back = traceback.extract_tb(ex_traceback)
            trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
            self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
            return None

    def received_after_check(self):
        return self.newest_tx_ts > self.utxos_last_updated

    def refresh_utxos(self):
        self.wallet.utxos_update()
        self.current_fees = self.get_btc_fee()
        self.wallet._balance_update()
        self.cmd.set_btc_bal(self.wallet.balance())
        self.utxos_last_updated = time.time()

    def add_msg(self, chatid, msg):
        send_ar = clean_msg(msg)
        for m in send_ar:
            self.q.put([chatid, m])

    async def future_remove_received_addrs(self, addr, time): #thread unsafe
        print(f'future remove addr {addr} in {time}')
        #if not self.profile.futures_str.has(key):
        #    self.profile.futures_str.add(addr, '|'.join([addr, 'deladdrs', str(int(time.time())), str(time)]))
        #    self.profile.backup_futures()
        if time > 0:
            await asyncio.sleep(time)
        try:
            self.received_addrs.remove(addr)
        except:
            pass
        self.futures.remove(addr)
        self.profile.futures_str.remove(addr)
        self.profile.backup_futures()

    def add_msg_from_txid(self, txid, msg):
        tx = self.cmd.pending_orders.get(txid)
        if tx:
            self.add_msg(tx[3], msg)

    async def bitaps(self, session, addr):
        async with self.bitapsbucket:
            try:
                res = await session.get(f"https://api.bitaps.com/btc/v1/blockchain/address/state/{addr}", proxy=self.proxy)
                if res.status == 200:
                    j = await res.json() #need to return with addr because api doesn't provide
                    j['address'] = addr
                    return j
                else:
                    return res
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
                return None

    async def blockchaininfo(self, session, addrs):
        async with self.bcbucket:
            try:
                res = await session.get(btc_addr_multi_url + '|'.join(addrs), proxy=self.proxy)
                return res
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
                return None

    async def check(self, futures):
        self.futures = futures
        async with ClientSession() as session:
            while not self.stop:
                iter = self.cmd.pendingBTC.copy()
                addresses = []
                for k, v in iter.items():
                    try:
                        if self.futures.has(k): #will be deleted after x amount of time. should remain being checked
                            addresses.append(k)
                            continue
                        if get_cur_ts() - v['created'] > 21600 and not v['received_funds']: #6 hours
                            self.cmd.cancel_order_alert(v['txid'], k)
                            continue
                        if get_cur_ts() - v['created'] > 86400:
                            #if v['received_funds']: #includes unconfirmed transactions
                            if btcprice < v['btc_price']: #recalculate with current price if price has gone down since last payment. raw remaining btc amount if price has stayed same or gone up
                                missing = v['price_fiat']-v['btc_price']*(v['received_amount'])
                                calc_missing = fiat_to_btc(missing)
                                expected = v['received_amount']+calc_missing
                                self.cmd.pendingBTC.get(k)['expected_amount_btc'] = expected
                                v['expected_amount_btc'] = expected
                            else:
                                calc_missing = v['expected_amount_btc']-v['received_amount']
                                expected = v['expected_amount_btc']
                            t = 86400
                            self.add_msg_from_txid(v['txid'], f"The address for your order #{v['txid']} is missing {'{:.8f}'.format(calc_missing)} BTC from the expected {'{:.8f}'.format(expected)} BTC. You have another 24 hours to meet the amount, in confirmed transactions, before the order is canceled. Please be aware that this amount does not include miner fees.")
                            self.cmd.send_admins(f"Order #{v['txid']} hasn't received the expected amount in BTC and will cancel after waiting another 24 hours. You are still able to manually verify the order during that time")
                            del_task = asyncio.ensure_future(self.cmd.delete_pending_in_future(v['txid'], k, t))
                            self.futures.add(v['txid'], del_task)
                            self.profile.futures_str.add(v['txid'], '|'.join([v['txid'], 'delpending', str(int(time.time())), str(t), k]))
                            self.log.backup('futures.txt', 'a', f"{v['txid']}|delpending|{int(time.time())+t}|{k}")
                            del_task2 = asyncio.ensure_future(self.future_remove_received_addrs(k, t))
                            self.futures.add(k, del_task2)
                            #self.profile.futures_str.add(k, '|'.join([k, 'deladdrs', str(int(time.time())), str(t)]))
                            #self.log.backup('futures.txt', 'a', f"{k}|deladdrs|{int(time.time())+t}")
                            continue
                        if k in self.received_addrs:
                            continue
                        addresses.append(k)
                    except:
                        ex_type, ex_value, ex_traceback = sys.exc_info()
                        trace_back = traceback.extract_tb(ex_traceback)
                        trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                        self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
                        print('Error in BTC.check()')
                addresses = [addresses[i:i+self.chunk_size] for i in range(0, len(addresses), self.chunk_size)]
                #while addresses:
                #    addrs = addresses.pop()
                #    res = await self.blockchaininfo(session, addrs)
                tasks = [asyncio.create_task(self.blockchaininfo(session, addrs)) for addrs in addresses]
                results = await asyncio.gather(*tasks)
                for res in results:
                    if res:
                        if res.status == 200:
                            j = await res.json()
                            for addr in j['addresses']:
                                d = self.cmd.pendingBTC.get(addr['address'])
                                if d == None:
                                    continue
                                d['received_funds'] = addr['total_received'] > 0
                                d['received_amount'] = addr['total_received']/100000000
                                d['received_confirmed'] = addr['n_tx'] > 0
                                if addr['n_tx'] > 0:
                                    self.newest_tx_ts = time.time()
                                if addr['final_balance'] >= d['expected_amount_btc']*100000000:
                                    d['received_full_amount'] = True
                                    self.received_addrs.add(addr['address'])
                                    #print(addr['address'] + ' moved to received_addrs')
                if len(results):
                    self.log.backup('pendingbtc.txt', 'w', '\n'.join([f'{k}|{ujson.dumps(v)}' for k, v in self.pendingBTC.__iter__().items()]))
                tasks = [asyncio.create_task(self.bitaps(session, addr)) for addr in self.received_addrs]
                results = await asyncio.gather(*tasks)
                for result in results:
                    if result:
                        if isinstance(result, dict):
                            bal = result['data']['balance']
                            if bal > 0:
                                address = result['address']
                                address = self.cmd.pendingBTC.get(address)
                                if not address: #if None because manually verified
                                    self.received_addrs.remove(result['address'])
                                    continue
                                if bal >= address['expected_amount_btc']*100000000: #confirmed  #only for safety, can be removed
                                    #address['received_full_amount'] = True
                                    #address['received_confirmed'] = True
                                    #address['received_amount'] = bal
                                    self.newest_tx_ts = time.time()
                                    txid = address['txid']
                                    self.cmd.payment_confirmed(txid, result['address'])
                                    self.received_addrs.remove(result['address'])
                                    self.cmd.update_btc_bal(bal)
                        #else:
                        #    print('error with bitaps')
                        #    print(await res.json())
                        #    print(res.status)
                            #log Error
                await asyncio.sleep(5)

    def start_handle(self, futures):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        asyncio.ensure_future(get_BTC_price(self.proxy))
        asyncio.ensure_future(self.check(futures))
        loop.run_forever()
        loop.close()
