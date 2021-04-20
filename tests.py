from queue import Queue
import asyncio, time, concurrent.futures, random, math, util
from aiohttp import ClientSession
from leakybucket import AsyncLimiter
from threading import Thread
from objects import SharedDict

bucket = AsyncLimiter(1, 0.34)
q = Queue()
ref = time.time()
[q.put(i) for i in range(100)]

##def add(q):
##    while True:
##        [q.put(i) for i in range(random.randint(5,15))]
##        time.sleep(1)
##
##t1 = Thread(target=add, args=(q,))
##t1.start()

def get_cur_ts():
    return math.floor(time.time())

##async def get_BTC_price():
##    async with ClientSession() as session:
##        while True:
##            ts = get_cur_ts()
##            async with session.get(f'https://www.blockchain.com/prices/api/coin-historical-price?fsym=BTC&tsyms=USD&ts={ts}&limit=1') as res:
##                j = await res.json()
##                print(j['BTC']['USD'])
##            await asyncio.sleep(5)

async def task(session, url):
    async with bucket:
##        async with ClientSession() as session:
        print(f'Sending! {time.time() - ref:>5.2f}')
        async with session.get(url) as res:
            j = await res.json()
            print(f'Received! {time.time() - ref:>5.2f}')
            return j

async def submit(coro):
    return asyncio.create_task(coro)

loop = asyncio.get_event_loop()
    
async def read(q):
    async with ClientSession() as session:
        while True:
            tasks = []
            while not q.empty() and len(tasks) < 100:
                t = q.get()
                #tasks.append(asyncio.run_coroutine_threadsafe(submit(task('https://jsonplaceholder.typicode.com/todos/1')), loop).result())
                tasks.append(asyncio.create_task(task(session, 'https://jsonplaceholder.typicode.com/todos/1')))
            if tasks:
                yield tasks
                tasks = []
    ##        while tasks:
    ##            print('t')
    ##            done, pending = loop.run_until_complete(asyncio.wait(tasks, return_when=concurrent.futures.FIRST_COMPLETED))
    ##            for job in done:
    ##                yield job.result()
            await asyncio.sleep(0.005)

##loop.run_until_complete(main())
async def main():
    async for tasks in read(q):
        print(f'grabbed {len(tasks)} tasks')
        await asyncio.gather(*tasks)
    ##    done, pending = concurrent.futures.wait(tasks, return_when=concurrent.futures.FIRST_COMPLETED)
    ##    print(done)
    ##    print(pending)

d = SharedDict({})

asyncio.ensure_future(util.get_BTC_price())
async def test():
    while True:
        print(util.btcprice)
        await asyncio.sleep(5)
asyncio.ensure_future(test())
loop.run_forever()
#loop.run_until_complete(main())
print('completed')
loop.close()
