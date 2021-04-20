import re, util, sys, random, threading, time, traceback, math, string, asyncio, os, ujson
from objects import SharedSet, SharedDict, SharedList
from queue import Queue
from aiohttp import ClientSession

class Command:

    def __init__(self, profile):
        self.lock = threading.Lock()
        self.profile = profile
        self.wd = profile.wd
        self.blacklist = profile.blacklist
        self.futures = profile.futures
        self.q = profile.q
        self.broadcastq = profile.broadcastq
        self.log = profile.log
        self.wallet = profile.wallet
        self.settings = profile.settings
        self.max_bot_orders = profile.max_orders
        #self.wallet.utxos_update() #in bot_profile
        self.wallet._balance_update()
        self.total_order_count = self.settings['total_order_count']
        self.max_orders_reached = self.total_order_count >= self.max_bot_orders
        self.settings['btc_wallet_balance'] = self.wallet.balance()
        self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))
        self.balance = self.settings['btc_wallet_balance']
        self.tab = str.maketrans(string.ascii_lowercase + string.ascii_uppercase,string.ascii_lowercase * 2) #for str.translate() - fast str to lowercase
        self.admins = [l.translate(self.tab) for l in self.readf('admins.txt').read().splitlines()] #list for ordering. main admin is index 0
        if len(self.admins) == 0:
            print('add admin user id to admins.txt')
            sys.exit()
        self.max_num_orders = self.settings['max_orders'] #maximum orders a user can have open at the same time
        self.faq = [s.split('|') for s in self.readf('faq.txt').read().splitlines()]
        self.admin_commands = open('admin_commands.txt', 'r').read().splitlines()
        self.user_commands = open('user_commands.txt', 'r').read().splitlines()
        self.products = dict(map((lambda info : (info[0], {'title':info[1], 'description': info[2], 'url':info[3], 'price': float(info[4]), 'quantity': int(info[5]), 'delivery_content': (None if info[6].translate(self.tab) == 'none' else info[6])})), [p.split('|') for p in self.readf('products.txt').read().splitlines()]))
        self.catalog_str = self.format_catalog_str()
        self.canceled = set(self.readf('canceled.txt').read().splitlines())
        self.log.backup('canceled.txt', 'w', '') #removing canceled below, don't need it anymore
        #check for empty lines
        self.processing = SharedDict(dict(map((lambda info : (info[0], info[1:])), [p.split('|') for p in self.readf('processing.txt').read().splitlines() if p and p.split('|')[0] not in self.canceled])))
        self.log.backup('processing.txt', 'w', '\n'.join(f'{k}|{"|".join([str(e) for e in v])}' for k, v in self.processing.__iter__().items()))
        self.pending_orders = SharedDict(dict(map((lambda info : (info[0], info[1:])), [p.split('|') for p in self.readf('pending.txt').read().splitlines() if p and not self.processing.has(p.split('|')[0]) and p.split('|')[0] not in self.canceled]))) #txid: [uid, itemid, f'{method}-{oaddress}', chatid, oquantity]
        self.log.backup('pending.txt', 'w', '\n'.join(f'{k}|{"|".join([str(e) for e in v])}' for k, v in self.pending_orders.__iter__().items()))
        #self.pendingBTC = SharedDict({})
        #for k, v in self.pending_orders.__iter__().items():
        #    if v[2].startswith('btc-'):
        #        self.pendingBTC.add_if_new(v[2].split('-')[1], {'txid':k, 'received_funds':False, 'received_full_amount'})
        #self.pendingBTC = SharedDict(dict(map((lambda info : (info[0], {'txid':info[1], 'received_funds':(info[2]=='True'), 'received_full_amount':(info[3]=='True'), 'received_confirmed':(info[4]=='True'), 'expected_amount_btc':float(info[5]), 'price_fiat':float(info[6]), 'received_amount':float(info[7]), 'created':int(info[8])})), [p.split('|') for p in self.readf('pendingbtc.txt').read().splitlines() if p.split('|')[1] not in self.canceled]))) #address: {'txid':txid, 'received_funds':False, 'received_full_amount':False, 'received_confirmed': False, 'txs':[], 'expected_amount_btc':btc_amount, 'price_fiat': price, 'btc_price':util.btcprice,  'received_amount': 0, 'created':ts}
        self.pendingBTC = SharedDict(dict(map((lambda info : (info[0], ujson.loads(info[1]))), [p.split('|') for p in self.readf('pendingbtc.txt').read().splitlines() if p and self.pending_orders.has(ujson.loads(p.split('|')[1])['txid']) and ujson.loads(p.split('|')[1])['txid'] not in self.canceled])))
        self.log.backup('pendingbtc.txt', 'w', '\n'.join([f'{k}|{ujson.dumps(v)}' for k, v in self.pendingBTC.__iter__().items()]))
        #{'txid':txid, 'received_funds':False, 'received_full_amount':False, 'received_confirmed': False, 'txs':[], 'expected_amount_btc':btc_amount, 'price_fiat': price, 'btc_price':util.btcprice,  'received_amount': 0, 'created':util.get_cur_ts()}
        self.open_orders = SharedDict({}) #uid: [TXIDs]
        for k, v in self.pending_orders.__iter__().items():
            self.open_orders.add_if_new(v[0], [])
            self.open_orders.get(v[0]).append(k)
        for k, v in self.processing.__iter__().items():
            self.open_orders.add_if_new(v[0], [])
            self.open_orders.get(v[0]).append(k)
        #self.pending_del = SharedSet(set()) #for canceled orders with automatically verified payment methods (BTC)
        self.expecting_addresses = SharedSet(set()) #txids prompted to provide addresses
        self.payment_methods = dict(map((lambda m : (m[0].translate(self.tab), m[1])), [p.split('|') for p in self.readf('pay.txt').read().splitlines()]))
        self.chatids = SharedDict(val = dict(map((lambda ids : (ids[0].translate(self.tab), ids[1])), [i.split('|') for i in self.readf('ids.txt').read().splitlines()])))
        self.test_addrs = self.readf('test_addresses.txt').read().splitlines()
        self.support_tickets = SharedDict(dict(map((lambda m : (m[0], [m[1], m[2]])), [p.split('|') for p in self.readf('support_tickets.txt').read().splitlines() if p])))
        self.open_tickets = SharedDict({}) #uid: [TXIDs]
        for k, v in self.support_tickets.__iter__().items():
            self.open_tickets.add_if_new(v[0], [])
            self.open_tickets.get(v[0]).append(k)
        self.update_max_order_check(add=False)

    def refresh(self, max_orders):
        with self.lock:
            self.max_orders_reached = self.max_orders_reached and max_orders <= self.max_bot_orders #if True and True
            self.max_bot_orders = max_orders

    def update_max_order_check(self, add = True):
        with self.lock:
            #update from last reset based on subscription
            if time.time() - self.settings['last_reset'] >= 2592000: #30 days
                self.settings['last_reset'] = util.get_cur_ts()
                self.total_order_count = 0
                self.max_orders_reached = False
                self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))
                print('monthly count reset')
            if not self.max_orders_reached and self.total_order_count >= self.max_bot_orders-1:
                self.send_admins('Your maximum monthly order count has been reached. You still have access to all commands. Users attempting to order will be prompted to open a support ticket for manual assistance with future orders.')
                self.max_orders_reached = True
            if add:
                self.total_order_count += 1
                self.settings['total_order_count'] += 1
                self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))
            return self.max_orders_reached

    def readf(self, file_name):
        if not os.path.exists(self.wd + file_name):
            open(self.wd + file_name, 'w')
        return open(self.wd + file_name, 'r')

    def update_btc_bal(self, amt):
        with self.lock:
            self.balance += amt
        self.settings['btc_wallet_balance'] = self.balance
        self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))

    def set_btc_bal(self, bal):
        with self.lock:
            self.balance = bal
            try:
                self.settings['btc_wallet_balance'] = bal #may not be thread safe
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
        self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))

    async def parse(self, cmd, uid, chatid):
        self.admin = uid in self.admins
        if not self.admin and len(cmd) > 250:
            return [False, 'You cannot send messages over 250 characters']
        if not self.admin and not re.match('^[ \/0-9a-zA-Z,-@\:\"]+$', cmd): #if not admin, check for illegal chars
            return [False, "Only accepts alphanumeric strings with commas, spaces, quotation marks, @, -, :, and /"]
        #self.chatids.add_if_new(uid, chatid)
        if not self.chatids.has(uid):
            self.chatids.add(uid, chatid)
            self.log.backup('ids.txt', 'a', f"{uid}|{chatid}")
            #append to ids.txt
        self.uid = uid
        self.chatid = chatid
        if cmd.startswith('/pi_'):
            self.cmd = '/pi'
            self.args = [cmd.split('_')[1]]
            return [True]
        args = re.compile(' \s*(?=(?:[^"]*"[^"]*")*[^"]*$)').split(cmd)
        self.cmd = args[0].translate(self.tab)
        self.args = []
        for a in args[1:]: #optimize
            if a.startswith('"') and a.endswith('"'):
                self.args.append(a[1:-1])
            else:
                self.args.append(a)
        #if self.admin and self.cmd == '/withdraw': #balance updates in util.BTC
        #    return [False, 'refresh_utxos', '/withdraw requires that your BTC wallet UTXOs be refreshed. This usually takes a few minutes. It will also give you the most updated balance.']
        return [True]

    def new_BTC_addr(self):
        k = self.wallet.new_key()
        return k.address

    def genTXID(self):
        length = (self.pending_orders.length() + self.processing.length())*2 #if half or more of range used
        m = 10**(math.ceil(math.log(length, 10)) if length > 3 else 3)
        randid = str(random.randint(0, m))
        c = 0
        while self.pending_orders.has(randid) or self.processing.has(randid) and c < m:
            randid = str(random.randint(0, m))
            c += 1
        if c >= m:
            print('no more room for new TXIDs')
            return str(-1)
        return randid

    def gen_item_id(self):
        randid = str(random.randint(0, 1000))
        c = 0
        while randid in self.products and c < 1000:
            randid = str(random.randint(0, 1000))
            c += 1
        if c >= 1000:
            print('no more room for new products')
            return str(-1)
        return randid

    def gen_support_id(self):
        randid = str(random.randint(0, 1000))
        c = 0
        while self.support_tickets.has(randid) and c < 1000:
            randid = str(random.randint(0, 1000))
            c += 1
        if c >= 1000:
            print('no more room for support tickets')
            return str(-1)
        return randid

    def new_order(self, uid, txid):
        try:
            self.open_orders.add_if_new(uid, [])
            open = self.open_orders.get(uid)
            open.append(txid)
            return True
        except:
            ex_type, ex_value, ex_traceback = sys.exc_info()
            trace_back = traceback.extract_tb(ex_traceback)
            trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
            self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')
            #log
            #return [False, f'Canceling pending delete task (order #{txid}) failed: {ex_value}', f'{ex_type} {ex_value} {"|".join(trc)}']
            return False

    def add_msg(self, chatid, msg):
        send_ar = util.clean_msg(msg)
        for m in send_ar:
            self.q.put([chatid, m])
        return [True]

    def add_msg_from_uid(self, uid, msg):
        chatid = self.chatids.get(uid)
        if chatid:
            return self.add_msg(chatid, msg)
        return [False]

    def send_admin(self, msg):
        return self.add_msg(self.chatids.get(self.admins[0]), msg)

    def broadcast(self, uids, msg): #rate limit: 30/second. will receive 429
        send_ar = util.clean_msg(msg)
        for uid in uids:
            chatid = self.chatids.get(uid)
            for m in send_ar:
                self.broadcastq.put([chatid, m])

    def send_admins(self, msg): #either list of admin chatids or can send to admin group
        return self.broadcast(self.admins, msg)

    async def delete_pending_in_future(self, key, addr, time): #addr - None if not relevant
        print(f'future pending delete {key} in {time}')
        #if not self.profile.futures_str.has(key):
        #    self.profile.futures_str.add(key, '|'.join([key, 'delpending', str(int(time.time())), str(time), addr]))
        #    self.profile.backup_futures()
        if time > 0:
            await asyncio.sleep(time)
        order = self.pending_orders.remove(key)
        if addr:
            self.pendingBTC.remove(addr)
        if order:
            self.change_quantity(order[1], order[4])
            self.add_msg(order[3], f'Your order #{key} has been canceled after waiting for remaining funds')
        self.send_admins(f"Order #{key} has been canceled and removed from pending after waiting {time} seconds")
        self.futures.remove(key)
        self.profile.futures_str.remove(key)
        self.profile.backup_futures()
        return True

    def payment_confirmed(self, txid, addr): #called from util.BTC - moves order from pending to processing unless payment has been manually verified
        if self.pendingBTC.has(addr): #if not, already manually verified or canceled
            self.pendingBTC.remove(addr)
            order = self.pending_orders.remove(txid)
            if order:
                #if order has static delivery method
                product = self.products[order[1]]
                if product['delivery_content']:
                    self.add_msg(order[3], f"Payment for order #{txid} has been confirmed. Here is your delivery: {product['delivery_content']}")
                    self.send_admins(f"Payment for order #{txid} has been confirmed and product was delivered automatically. The order has been archived. You can edit/remove a product's delivery content with...")
                    self.log.backup('archived.txt', 'a', f'{txid}|{"|".join([str(e) for e in order])}')
                else:
                    self.add_msg(order[3], f"Payment for order #{txid} has been confirmed and your order is being processed. You must use the /addaddress command to link a delivery address to this order. The type of address (email, physical, username, etc.) should be specified in the product description")
                    self.send_admins(f"Payment for order #{txid} has been confirmed and customer has been prompted to provide delivery address.")
                    self.processing.add(txid, order)
                    self.log.backup('processing.txt', 'a', f'{txid}|{"|".join([str(e) for e in order])}')
            self.log.backup('canceled.txt', 'a', txid) #for loading pending/processing in init
            if self.futures.has(txid) or self.futures.has(addr):
                self.futures.remove(txid)
                self.profile.futures_str.remove(txid)
                self.futures.remove(addr)
                self.profile.futures_str.remove(addr)
                self.profile.backup_futures()
        #already manually verified or canceled

    def cancel_task(self, txid):
        try:
            t = self.futures.get(txid)
            if t != None:
                t.cancel()
            return [True]
        except:
            ex_type, ex_value, ex_traceback = sys.exc_info()
            trace_back = traceback.extract_tb(ex_traceback)
            trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
            return [False, f'Canceling pending delete task (order #{txid}) failed: {ex_value}', f'{ex_type} {ex_value} {"|".join(trc)}']

    def cancel_order(self, txid, addr = None):
        order = self.pending_orders.remove(txid)
        if not order:
            order = self.processing.remove(txid)
        if order:
            uid = order[0].translate(self.tab)
            self.open_orders.get(uid).remove(txid)
            self.change_quantity(order[1], order[4])
            #self.catalog_str = self.format_catalog_str() #just prints titles/categories now
        self.pendingBTC.remove(addr)
        self.log.backup('canceled.txt', 'a', txid)
        return order

    def cancel_order_alert(self, txid, addr): #called from util.BTC
        try:
            order = self.cancel_order(txid, addr)
            if order:
                self.send_admins(f'Order #{txid} has not received a payment after 6 hours and is canceled')
                self.add_msg(order[3], f'Your order #{txid} has not received a payment after 6 hours and is canceled')
        except:
            ex_type, ex_value, ex_traceback = sys.exc_info()
            trace_back = traceback.extract_tb(ex_traceback)
            trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
            self.log.log('Err', f'{ex_type} {ex_value} {"|".join(trc)}')

    def send_order_msg(self, ar): #ar = [txid, itemID, uid, order_quantity, paymethod]
        check = (ar[4] == 'btc')
        payflag = 'You will be notified when the transaction confirms\n'*check + f'Look for a payment with {ar[0]}|{ar[1]} in it\'s notes'*(not check)
        msg = f'''New order:
            txid: {ar[0]}
            itemID: {ar[1]}
            Customer: {ar[2]}
            Order quantity: {ar[3]}
            Payment method: {ar[4]}
            Timestamp: {util.get_cur_ts()}
            {payflag}
            '''
        return self.send_admins(msg)

    def format_catalog_str(self):
        if not len(self.products):
            return 'Catalog is empty'
        s = []
        for k in self.products:
            info = self.products[k]
            s.append(f'{k} - Title: {info["title"]}\nMore info: /pi_{k}')
        return '\n'.join(s)
        '''
        for k in self.products:
            info = self.products[k]
            quantity = (info['quantity'] == 0)*'Out of stock' + (info['quantity'] == -1)*'Infinite' #conditionless flags
            quantity += (len(quantity) == 0)*str(info['quantity']) #when length of quantity is 0, info['quantity'] is added
            s.append(f"{k} - Title: {info['title']}\nDescription: {info['description']}\nImage(s): {info['url']}\nQuantity: {quantity}\nPrice in usd: {info['price']}\n")
        return '\n\n'.join(s)
        '''

    def change_quantity(self, itemid, oq):
        if self.products[itemid]['quantity'] == 0:
            return
        if self.products[itemid]['quantity'] == -1:
            return
        try:
            self.products[itemid]['quantity'] += oq
        except:
            pass

    async def checkBTC(self, address):
        async with ClientSession() as session:
            async with session.get() as res:
                pass

    async def serve(self): #return True/False in case of webhook in future
        if self.admin:
            if self.cmd == '/help':
                try:
                    return [True, '\n'.join(self.admin_commands) + '\n'*2 + '\n'.join(self.user_commands)]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/addfaq':
                try:
                    if len(self.args) != 2:
                        return [True, 'Format like this: /addfaq "QUESTION" "ANSWER"']
                    if '|' in ''.join(self.args):
                        return [True, "FAQ updates cannot contain the vertical bar character '|'"]
                    self.faq.append([self.args[0], self.args[1]])
                    self.log.backup('faq.txt', 'a', f'{self.args[0]}|{self.args[1]}'.replace("\n", '\\n'))
                    return [True, 'Added QA pair to FAQ']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/editfaq': #must rewrite faq.txt to preserve position of edited entry
                try:
                    if len(self.args) != 3:
                        return [True, 'Format like this: /editfaq FAQ_NUMBER "NEW QUESTION" "NEW ANSWER" - You can use a "-" in the place of either the new question or new answer argument to keep that part the same']
                    entry = self.faq[int(self.args[0])]
                    if self.args[1] != '-':
                        entry[0] = self.args[1]
                    if self.args[2] != '-':
                        entry[1] = self.args[2]
                    self.log.backup('faq.txt', 'w', '\n'.join(['|'.join(qa).replace("\n", '\\n') for qa in self.faq]))
                    return [True, 'FAQ entry successfully edited']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/delfaq':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /delfaq FAQ_NUMBER']
                    self.faq.pop(int(self.args[0]))
                    self.log.backup('faq.txt', 'w', '\n'.join(['|'.join(qa).replace("\n", '\\n') for qa in self.faq]))
                    return [True, 'FAQ entry successfully deleted']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, "Error deleting from faq: " + str(ex_value), f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/addblacklist':
                try:
                    if len(self.args) != 2:
                        return [True, 'Format like this: /addblacklist USERNAME H:M:S - Ex: /addblacklist spammer 24:0:0 (adds spammer to blacklist for 24 hours. use 0:0:0 to perm ban user. you can use /rmblacklist to unban a user)']
                    uid = self.args[0].translate(self.tab)
                    if self.uid == uid or uid in self.admins:
                        return [True, 'You cannot blacklist yourself or another admin']
                    t = [int(i) for i in self.args[1].split(':')]
                    seconds = t[0]*3600 + t[1]*60 + t[2]
                    done = time.time()+seconds
                    self.blacklist.add(uid, done)
                    self.log.backup('blacklist.txt', 'a', f'{uid}|{done}')
                    return [True, f"{self.args[0]} added to blacklist for {seconds} seconds"]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/rmblacklist':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /rmblacklist USERNAME']
                    uid = self.args[0].translate(self.tab)
                    rm = self.blacklist.remove(uid)
                    if rm:
                        self.log.backup('blacklist.txt', 'w', '\n'.join([f'{k}|{v}' for k, v in self.blacklist.copy().items()]))
                        return [True, f"{self.args[0]} removed from blacklist"]
                    return [True, f"{self.args[0]} wasn't blacklisted"]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/blacklist':
                try:
                    if self.blacklist.length() == 0:
                        return [True, 'Blacklist is empty']
                    return [True, 'UserID - Timestamp\n' + '\n'.join([f"{k} - {v}" for k, v in self.blacklist.copy().items()])]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/addproduct':
                try:
                    if 5 > len(self.args) or len(self.args) > 6:
                        return [False, 'Format like this: /addproduct "TITLE" "DESC" "IMG_URL" PRICE_USD QUANTITY (-1 if infinite) ("DELIVERY_CONTENT") (link/code/etc. if any, can be edited)']
                    iid = self.gen_item_id()
                    if iid == '-1':
                        return [False, 'No room for new products']
                    self.products[iid] = {'title':self.args[0], 'description': self.args[1], 'url':self.args[2], 'price': float(self.args[3]), 'quantity': int(self.args[4]), 'delivery_content': (self.args[5] if len(self.args) == 6 else None)}
                    self.catalog_str = self.format_catalog_str()
                    self.log.backup('products.txt', 'a', f'{iid}|' + '|'.join(self.args).replace("\n", '\\n') + '|none'*(len(self.args)==5)) #f'{iid}|' + '|'.join(self.args) + '|none'*len(self.args)==5 - returned boolean for some reason
                    return [True, 'Successfully added product']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Error adding product', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/delproduct':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /delproduct ITEM_ID']
                    self.products.pop(self.args[0])
                    self.catalog_str = self.format_catalog_str()
                    self.log.backup('products.txt', 'w', '\n'.join([f'{k}|{"|".join([str(e) for e in v.values()])}'.replace("\n", "\\n") for k, v in self.products.copy().items()]))
                    return [True, 'Successfully deleted product']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Error deleting product', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/editproduct':
                try:
                    if 6 > len(self.args) or len(self.args) > 7:
                        return [True, 'Format like this: /editproduct ITEM_ID "TITLE" "DESC" "IMG_URL" PRICE_USD QUANTITY ("DELIVERY_CONTENT")']
                    product = self.products[self.args[0]]
                    if self.args[1] != '-':
                        product['title'] = self.args[1]
                        self.catalog_str = self.format_catalog_str()
                    if self.args[2] != '-':
                        product['description'] = self.args[2]
                    if self.args[3] != '-':
                        product['url'] = self.args[3]
                    if self.args[4] != '-':
                        product['price'] = float(self.args[4])
                    if self.args[5] != '-':
                        product['quantity'] = int(self.args[5])
                    if len(self.args) == 7:
                        if self.args[6] != '-':
                            product['delivery_content'] = self.args[6]
                    else:
                        product['delivery_content'] = None
                    self.log.backup('products.txt', 'w', '\n'.join([f'{k}|{"|".join([str(e) for e in v.values()])}'.replace("\n", "\\n") for k, v in self.products.copy().items()]))
                    return [True, 'Product was successfully edited']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Error editing product', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/setquantity':
                try:
                    if len(self.args) != 2:
                        return [True, 'Format like this: /setquantity ITEM_ID QUANTITY']
                    self.products[self.args[0]]['quantity'] = int(self.args[1])
                    #self.catalog_str = self.format_catalog_str()
                    self.log.backup('products.txt', 'w', '\n'.join([f'{k}|{"|".join([str(e) for e in v.values()])}'.replace("\n", "\\n") for k, v in self.products.copy().items()]))
                    return [True, 'Successfully changed quantity']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Error changin product quantity', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/broadcast':
                try:
                    #set self.contacts
                    if len(self.args) != 1:
                        return [True, "Only provide the necessary arguments"]
                    self.broadcast(self.chatids.keys(), self.args[0])
                    return [True, "Finished broadcasting to all bot contacts"]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/addadmin': #case sensitive
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /addadmin USERNAME']
                    if self.uid != self.admins[0]:
                        return [True, 'Only the genesis admin or proceeding main admins may perform this command']
                    usr = self.args[0].translate(self.tab)
                    if usr not in self.admins:
                        self.admins.append(usr)
                    else:
                        return [True, f'{self.args[0]} is already an admin']
                    self.log.backup('admins.txt', 'a', usr)
                    return [True, f'Added {self.args[0]} to admins']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Error adding admin', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/deladmin':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /deladmin USERNAME']
                    if self.uid != self.admins[0]:
                        return [True, 'Only the genesis admin or proceeding main admins may perform this command']
                    usr = self.args[0].translate(self.tab)
                    if len(self.admins) == 1:
                        return [True, 'There is only 1 admin in admins, cannot perform command']
                    if self.args[0] in self.admins:
                        self.admins.remove(self.args[0])
                    else:
                        return [True, 'Member specified is not recognized as an admin.']
                    self.log.backup('admins.txt', 'w', '\n'.join(self.admins))
                    return [True, f'Successfully removed {self.args[0]} from admins']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Error performing /deladmin', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/setmainadmin':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /setmainadmin USERNAME']
                    if self.uid != self.admins[0]:
                        return [True, 'Only the genesis admin or proceeding main admins may perform this command']
                    usr = self.args[0].translate(self.tab)
                    if usr in self.admins:
                        if self.admins[0] == usr:
                            return [True, f'{self.args[0]} is already the main admin']
                        temp = self.admins[0]
                        self.admins[self.admins.index(usr)] = temp
                        self.admins[0] = usr
                        self.log.backup('admins.txt', 'w', '\n'.join(self.admins))
                        return [True, f'Made {self.args[0]} main admin']
                    else:
                        return [True, f'Make {self.args[0]} an admin before making them main admin']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/admins':
                try:
                    return [True, '\n'.join(self.admins)]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/verify':
                try:
                    if 0 == len(self.args) or len(self.args) > 2:
                        return [True, 'Format like this: /verify ORDER_ID *DELIVERY_CONTENT (optional)']
                    txid = self.args[0]
                    is_digital = len(self.args) == 2
                    rm = self.pending_orders.remove(txid)
                    cancel = self.cancel_task(txid) #returns flag if care to process
                    if not rm:
                        is_processing = self.processing.has(txid)
                        return [True, f'This order {"(if existed) was canceled by the customer (they are given an admin contact address to message)" if not is_processing else "is already verified and is in processing"}']
                    if rm[2].startswith('btc'): #can remove check
                        oaddress = rm[2].split('-')[1]
                        self.pendingBTC.remove(oaddress)
                    customer_chatid = self.chatids.get(rm[0])
                    if is_digital: #uses delivery_content passed to /verify over content from /addproduct or /editproduct if any
                        self.add_msg(customer_chatid, f'Your order #{txid} has been verified. Here is your product: {self.args[1]}. Use /support if you believe there is something wrong with this delivery')
                        self.open_orders.get(rm[0]).remove(txid)
                        self.log.backup('archived.txt', 'a', f'{txid}|{"|".join([str(e) for e in rm])}')
                        self.log.backup('canceled.txt', 'a', txid) #for loading pending/processing in init
                        return [True, f'Order #{txid} verified and product delivered']
                    product = self.products[rm[1]]
                    if product['delivery_content']:
                        self.add_msg(customer_chatid, f'Your order #{txid} has been verified. Here is your product: {product["delivery_content"]}. Use /support if you believe there is something wrong with this delivery')
                        self.open_orders.get(rm[0]).remove(txid)
                        self.log.backup('archived.txt', 'a', f'{txid}|{"|".join([str(e) for e in rm])}')
                        self.log.backup('canceled.txt', 'a', txid) #for loading pending/processing in init
                        return [True, f'Order #{txid} verified and product auto delivered']
                    self.processing.add(txid, rm)
                    self.add_msg(customer_chatid, f'Your order #{txid} has been verified. Use the /addaddress command to link a delivery address (physical or virtual). Type should be specified in the product description. Do /catalog if are unsure.')
                    self.log.backup('processing.txt', 'a', f'{txid}|{"|".join([str(e) for e in rm])}')
                    self.log.backup('canceled.txt', 'a', txid) #for loading pending/processing in init
                    return [True, f'Order #{txid} verified and customer prompted to provide address']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/finish':
                try:
                    if 1 > len(self.args) > 2:
                        return [True, 'Format like this: /finish TXID (MESSAGE (if any)) - message will be delivered to customer. if you mailed the product, you can put tracking information here']
                    oar = self.processing.remove(self.args[0])
                    if not oar:
                        return [True, f'Payment for order #{self.args[0]} was never confirmed and order is still in pending. You can use /cancelorder if you want to cancel it']

                    self.add_msg(oar[3], f"Your order #{self.args[0]} has been marked finished and is therefore delivered or being delivered. Use /support if you believe this is a mistake." + (len(self.args) == 2)*f' Here are extra notes related to the order: {self.args[1]}')
                    self.log.backup('archived.txt', 'a', f'{self.args[0]}|{"|".join([str(e) for e in oar])}')
                    self.log.backup('canceled.txt', 'a', self.args[0]) #again just in case
                    return [True, f'Successfully archived order #{self.args[0]}' + (len(self.args) == 2)*' and delivered message to customer']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/orderinfo':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /orderinfo ORDER_ID']
                    if self.pending_orders.has(self.args[0]):
                        order = self.pending_orders.get(self.args[0])
                        msg = f'''\nCustomer: {order[0]}\nItemID: {order[1]}\nPaymethod: {order[2]}\nOrder quantity: {order[4]}\nDelivery address: {order[5] if len(order) == 6 else "not yet provided or not needed"}'''
                        return [True, f"Order #{self.args[0]} is in pending_orders. Info: {msg}"]
                    elif self.processing.has(self.args[0]):
                        order = self.processing.get(self.args[0])
                        msg = f'''\nCustomer: {order[0]}\nItemID: {order[1]}\nPaymethod: {order[2]}\nOrder quantity: {order[4]}\nDelivery address: {order[5] if len(order) == 6 else "not yet provided or not needed"}'''
                        return [True, f"Order #{self.args[0]} is in processing. Info: {msg}"]
                    else:
                        return [True, "Order not found"]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/openorders':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /openorders UID']
                    open = self.open_orders.get(self.args[0])
                    return [True, ', '.join(open) if open and len(open) else 'User has no open orders']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/processing':
                try:
                    if self.processing.length() == 0:
                        return [True, 'Processing is empty']
                    if self.processing.length() > 25:
                        return [True, f'Processing size is over 25, you can use /orderinfo to get more info on these order ids:\n{",".join(self.processing.keys())}']
                    else:
                        #temp = self.processing.copy()
                        #ids = temp.keys()
                        #orders = temp.values()
                        #formatted = []
                        #for order in orders:
                        #    formatted.append(','.join([f"{k}: {v}" for k, v in zip(order.keys(), order.values())]))
                        #return [True, '\n'.join([f"{id}: {order}" for id, order in zip(ids, formatted)])]
                        formatted = []
                        temp = self.processing.copy()
                        for k in temp:
                            v = temp[k]
                            msg = f'''txid: {k}, itemID: {v[1]}, Custom telegram user id: {v[0]}, Order quantity: {v[4]}, Payment method: {v[2]}''' + (f', Customer delivery address: {v[5]}' if len(v) == 6 else '')
                            formatted.append(msg)
                        return [True, '\n'.join(formatted)]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']

            elif self.cmd == '/resupport':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /resupport SUPPORT_ID']
                    id = self.args[0]
                    ticket = self.support_tickets.remove(id)
                    if ticket:
                        uid = ticket[0]
                        self.open_tickets.get(uid).remove(id) #no reason why it should except
                        self.add_msg_from_uid(uid, f"Your support ticket #{id} is being responded to. Look for a chat from admin @{self.uid}")
                        self.log.backup('support_tickets.txt', 'w', '\n'.join([f'{k}|{v[0]}|{v[1]}' for k, v in self.support_tickets.copy().items()]))
                        return [True, f'This ticket has been removed from open tickets and @{uid} has been notified to look for a chat from your username. Add @{uid} to chat.']
                    return [True, 'This ticket does not exist']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/viewticket':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /viewticket SUPPORT_ID']
                    id = self.args[0]
                    if self.support_tickets.has(id):
                        ticket = self.support_tickets.get(id)
                        return [True, f'{ticket[0]} - {ticket[1]}']
                    else:
                        return [True, 'This ticket does not exist']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/opensupports':
                try:
                    if self.support_tickets == 0:
                        return [True, 'There are no open support requests']
                    if self.support_tickets.length() > 25:
                        return [True, f'There are more than 25 open support tickets. Use /viewticket to display info on relevant ids: {", ".join(self.support_tickets.keys())}']
                    return [True, "\n".join([f'{k} - ({v[0]}) {v[1]}' for k, v in self.support_tickets.copy().items()])]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/addpaymethod':
                try:
                    if len(self.args) != 2:
                        return [True, 'Format like this: /addpaymethod NAME ADDRESS - Names are case in-sensitive']
                    if not re.match('^[a-zA-Z0-9]+$', self.args[0]):
                        return [True, 'Payment method name should only contain letters and numbers']
                    name = self.args[0].translate(self.tab)
                    if name in self.payment_methods:
                        return [True, 'You must choose a different name']
                    self.payment_methods[name] = self.args[1]
                    #i = [i for i, e in enumerate(self.faq) if e[0] == 'Available payment methods'][0]
                    #self.faq[i][1] += ', ' + self.args[1]
                    #self.log.backup('faq.txt', 'w', '\n'.join(['|'.join(qa) for qa in self.faq]))
                    self.log.backup('pay.txt', 'a', f'{name}|{self.args[1]}')
                    return [True, 'Successfully added payment method. Remember to add the name to the FAQ']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/delpaymethod':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /delpaymethod NAME']
                    name = self.args[0].translate(self.tab)
                    if name not in self.payment_methods:
                        return [True, 'This pay method was not found']
                    self.payment_methods.pop(name)
                    self.log.backup('pay.txt', 'w', '\n'.join(f'{k}|{v}' for k, v in self.payment_methods.items()))
                    return [True, 'Successfully deleted payment method. Remember to delete the name from the FAQ']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/setmaxorders':
                try:
                    if len(self.args) != 1:
                        return [True, 'Format like this: /setmaxorders NUM']
                    self.max_num_orders = int(self.args[0])
                    self.settings['max_orders'] = self.max_num_orders
                    self.log.backup('persistent.json', 'w', ujson.dumps(self.settings))
                    return [True, 'Max order count successfully changed']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/withdraw':
                try:
                    #if 2 > len(self.args) or len(self.args) > 3:
                    #    return [True, 'Format like this: /withdraw ADDRESS AMOUNT (FEE - integer - sat/byte)- This CANNOT be undone! Fee is grabbed automatically based on current network activity. You can optionally provide a fee if you want to override the automatic fee or it wasn\'t able to be grabbed. You can use "all" in place of AMOUNT to withdraw your total balance. Otherwise, AMOUNT should be in decimal format.']
                    if 1 > len(self.args) or len(self.args) > 2:
                        return [True, 'Format like this: /withdraw ADDRESS (FEE - integer - sat/byte) - Withdraw your BTC balance to an address. This CANNOT be undone! Fee is grabbed automatically based on current network activity. You can optionally provide a fee if you want to override the automatic fee or if it wasn\'t able to be grabbed']
                    if self.uid != self.admins[0]:
                        return [True, 'Only the genesis admin or proceeding main admins can withdraw']
                    #fee = await util.get_btc_fee() if len(self.args) != 3 else int(self.args[2])
                    #if not fee:
                    #    return [False, "Fee couldn't be grabbed automatically. Please provide one"]
                    fee = None
                    if len(self.args) == 2:
                        fee = int(self.args[1])
                        if fee <= 0:
                            return [True, 'Fee must be more than 0']
                    #if not fee:
                    #    return [True, 'Fee either couldn\'t be grabbed automatically or can\'t be 0. Please provide it manually']
                    #if amount  == 'all':
                    #    amount = self.wallet.balance()
                    #else:
                    #    amount = int(float(self.args[1])*100000000)
                    return [True, 'create_transaction', (self.args[0], fee)]
                    #if self.args[1] == 'all':
                    #    tx = self.wallet.send_to(self.args[0], self.balance*100000000, Fee=fee)
                    #    with self.lock:
                    #        self.balance = 0
                    #else:
                    #    amt = float(self.args[1])*100000000
                    #    if amt > self.balance*100000000:
                    #        return [True, 'Including fees, you do not have this balance available']
                    #    tx = self.wallet.send_to(self.args[0], amt, Fee=fee)
                    #    self.update_btc_bal(-amt)
                    #return [True, f'Successfully withdrew {self.args[1]} BTC. Here is your transaction: {tx}']
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
            elif self.cmd == '/balance':
                try:
                    refresh = self.profile.btc.received_after_check() and not self.profile.utxos_thread.is_alive()
                    if refresh:
                        self.profile.utxos_thread = threading.Thread(target=self.profile.btc.refresh_utxos) #assignment may not be thread safe
                        self.profile.utxos_thread.daemon = True
                        self.profile.utxos_thread.start()
                    return [True, f'You have {"{:.8f}".format(self.balance/100000000)} BTC' + (refresh or self.profile.utxos_thread.is_alive())*". Wallet UXTOs are currently being updated. You can try again in a few minutes for a more accurate balance"]
                except:
                    ex_type, ex_value, ex_traceback = sys.exc_info()
                    trace_back = traceback.extract_tb(ex_traceback)
                    trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                    return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        #user commands
        #package max order limit reached:
        #if self.max_orders_reached_check and self.cmd in ['temporarily disabled commands like /order'], send user admin contact for manual completion
        if self.update_max_order_check(add=False) and self.cmd == '/order':# in ['/order']:
            return [True, 'Use /support to request manual assistance in creating an order']

        if self.cmd == '/help':
            try:
                return [True, '\n'.join(self.user_commands)]
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/support': #add support ticket to self.support_tickets
            try:
                if len(self.args) != 1:
                    return [True, 'Format like this: /support "MESSAGE". Message must be less than 100 characters and should be surrounded by quotes ""']
                if len(self.args[0]) > 100:
                    return [True, f'Support messages must be less than 100 characters. Yours was: {len(self.args[0])} characters long']
                if self.open_tickets.has(self.uid):
                    if len(self.open_tickets.get(self.uid)) >= 3:
                        return [True, 'You cannot have more than 3 support tickets open']
                else:
                    self.open_tickets.add(self.uid, [])

                support_id = self.gen_support_id()
                if support_id == '-1':
                    return [True, 'No room for new support tickets']
                self.support_tickets.add(support_id, [self.uid, self.args[0]])
                self.open_tickets.get(self.uid).append(support_id)
                self.log.backup('support_tickets.txt', 'a', f'{support_id}|{self.uid}|{self.args[0]}')
                self.send_admins(f"{self.uid} has opened a support ticket (ID: {support_id}). Use /resupport to respond. Message: {self.args[0]}")
                return [True, f'Your support ticket ID is {support_id}']
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/delticket':
            try:
                if len(self.args) != 1:
                    return [True, 'Format like this: /delticket SUPPORT_ID']
                id = self.args[0]
                if self.admin:
                    if not self.support_tickets.has(id):
                        return [True, 'This ticket does not exist']
                    ticket = self.support_tickets.remove(id)
                    if ticket:
                        uid = ticket[0]
                        try:
                            self.open_tickets.get(uid).remove(id)
                        except:
                            pass
                        self.log.backup('support_tickets.txt', 'w', '\n'.join([f'{k}|{v[0]}|{v[1]}' for k, v in self.support_tickets.copy().items()]))
                        self.add_msg_from_uid(uid, f'Your support ticket #{id} has been deleted. Use /support if you believe this is a mistake')
                        return [True, 'Ticket has been deleted and user has been notified']
                open = self.open_tickets.get(self.uid)
                if open and id in open:
                    open.remove(id)
                    self.support_tickets.remove(id)
                    self.log.backup('support_tickets.txt', 'w', '\n'.join([f'{k}|{v[0]}|{v[1]}' for k, v in self.support_tickets.copy().items()]))
                    self.send_admins(f'{self.uid} has deleted their support ticket #{id}')
                    return [True, 'Support ticket deleted']
                else:
                    return [True, 'This ticket does not exist']
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/catalog':
            try:
                return [True, self.catalog_str]
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/pi': #product info
            try:
                if len(self.args) != 1:
                    return [True, 'Format like this: /pi ITEM_ID']
                if self.args[0] not in self.products:
                    return [True, 'Select a product in the catalog']
                info = self.products[self.args[0]]
                quantity = (info['quantity'] == 0)*'Out of stock' + (info['quantity'] == -1)*'Infinite' #conditionless flags
                quantity += (len(quantity) == 0)*str(info['quantity']) #when length of quantity is 0, info['quantity'] is added
                return [True, f"Title: {info['title']}\nDescription: {info['description']}\nImage(s): {info['url']}\nQuantity: {quantity}\nPrice in usd: {info['price']}\n"]
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/order':
            try:
                if len(self.args) != 3:
                    return [False, 'Format the command like this: /order ITEM_ID QUANTITY PAY_METHOD']
                uorders = self.open_orders.get(self.uid)
                if not self.admin and uorders and len(uorders) >= self.max_num_orders:
                    return [False, 'You cannot have more than the maximum number of open orders']
                itemid = self.args[0]
                if itemid not in self.products:
                    return [False, 'Select a product in the catalog']
                try:
                    oquantity = int(round(float(self.args[1]))) #no decimals
                except:
                    return [False, 'Quantity must be a positive integer']
                if oquantity <= 0:
                    return [False, 'Quantity must be a positive integer']
                product = self.products[itemid]
                if product['quantity'] > -1 and oquantity > product['quantity']:
                    return [False, 'This item is either out of stock or your quantity is too high']
                method = self.args[2].translate(self.tab)
                if method != 'btc' and method not in self.payment_methods:
                    return [False, 'The payment method you specified is not available']

                #for i in range(100):
                txid = self.genTXID()
                if txid == '-1':
                    return [False, 'No room for new orders']
                self.new_order(self.uid, txid)
                price = product['price']*oquantity
                self.add_msg(self.chatid, f'Your order ID is {txid}')
                self.change_quantity(itemid, -oquantity)
                if method == 'btc':
                    oaddress = self.new_BTC_addr()
                    #oaddress = self.test_addrs[0]
                    oar = [self.uid, itemid, f'btc-{oaddress}', self.chatid, oquantity]
                    self.pending_orders.add(txid, oar)
                    btc_amount = util.fiat_to_btc(price)
                    self.add_msg(self.chatid, f'Send {"{:.8f}".format(btc_amount)} BTC to this btc address:')
                    self.add_msg(self.chatid, oaddress)
                    #{txid, received_funds (boolean), received_full_amount (boolean), [txs], expected_amount}
                    tmp = {'txid':txid, 'received_funds':False, 'received_full_amount':False, 'received_confirmed': False, 'txs':[], 'expected_amount_btc':btc_amount, 'price_fiat': price, 'btc_price':util.btcprice,  'received_amount': 0, 'created':util.get_cur_ts()}
                    self.pendingBTC.add(oaddress, tmp) #can convert to list, saves ~3x mem. update util.BTC
                    self.log.backup('pending.txt', 'a', f'{txid}|{"|".join([str(e) for e in oar])}')
                    self.log.backup('pendingbtc.txt', 'a', f'{oaddress}|{ujson.dumps(tmp)}')
                else:
                    oaddress = self.payment_methods[method]
                    oar = [self.uid, itemid, f"{method}-{oaddress}", self.chatid, oquantity]
                    self.pending_orders.add(txid, oar)
                    self.add_msg(self.chatid, f'You MUST send your payment with this in the message/memo area: "{txid}|{itemid}". Send ${price} USD to this address:')
                    self.add_msg(self.chatid, oaddress)
                    self.log.backup('pending.txt', 'a', f'{txid}|{"|".join([str(e) for e in oar])}')
                #self.catalog_str = self.format_catalog_str()
                self.send_order_msg([txid, itemid, self.uid, oquantity, method])
                self.update_max_order_check()
                #await asyncio.sleep(0.01)
                return [True]
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/cancelorder':
            try:
                if len(self.args) != 1:
                    return [True, 'Format like this: /cancelorder ORDER_ID']
                txid = self.args[0]
                order = self.pending_orders.get(txid)
                if not order:
                    if self.processing.has(txid):
                        if self.admin:
                            order = self.cancel_order(txid)
                            uid = order[0].translate(self.tab)
                            self.add_msg(self.chatids.get(uid), 'Your order has been canceled. Use /support if you believe this is a mistake')
                            return [True, f'This order has been canceled and removed from processing. The customer has been notified. Their username: {uid}']
                        return [False, f"This order is already being processed and is too late to cancel. Use /support to open a support ticket for help"]
                    else:
                        return [True, 'This order does not exist']
                elif self.admin:
                    order = self.cancel_order(txid)
                    uid = order[0].translate(self.tab)
                    self.add_msg(self.chatids.get(uid), 'Your order has been canceled. Use /support if you believe this is a mistake')
                    return [True, f'This order has been canceled and removed from pending. The customer has been notified. Their username: {uid}']
                uid = order[0].translate(self.tab)
                if self.uid.translate(self.tab) != uid:
                    return [False, 'This order does not exist'] #misinformation
                open = self.open_orders.get(uid)
                if not open or not txid in open: #open.contains(txid):
                    return [False, 'This order does not exist'] #genuine
                self.cancel_order(txid)
                self.send_admins(f"Order #{txid} has been canceled by customer")
                return [True, f'Successfully canceled order {txid}']
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/openorders':
            try:
                open = self.open_orders.get(self.uid)
                return [True, ', '.join(open) if open and len(open) else 'You have no open orders']
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/faq':
            try:
                return [True, '\n'.join([f'{x}. Q - {e[0]}\nA - {e[1]}' for x, e in enumerate(self.faq) if len(e) == 2])]
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        elif self.cmd == '/addaddress':
            try:
                if len(self.args) != 2:
                    return [True, 'Format like this: /addaddress ORDER_ID "ADDRESS"']
                open = self.open_orders.get(self.uid)
                if not open or not self.args[0] in open: #open.contains(self.args[0]):
                    return [True, 'Order does not exist']
                if not self.processing.has(self.args[0]):
                    return [True, 'Order not yet verified']
                got = self.processing.get(self.args[0])
                if len(got) == 5: #has uid, itemid, method, chatid, quantity
                    got.append(self.args[1])
                else:
                    got[5] = self.args[1]
                self.send_admins(f'{self.uid} has provided a delivery address for order #{self.args[0]}: {self.args[1]}')
                return [True, 'You have linked a delivery address to your order. You can change the address by performing the same command']
            except:
                ex_type, ex_value, ex_traceback = sys.exc_info()
                trace_back = traceback.extract_tb(ex_traceback)
                trc = [f'File: {t[0]}, Line: {t[1]}, Func.Name: {t[2]}, Message: {t[3]}' for t in trace_back]
                return [False, 'Something went wrong...', f'{ex_type} {ex_value} {"|".join(trc)}']
        return [True, 'Unknown command. Try /help']
