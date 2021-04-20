from queue import Queue

q = Queue()
q2 = Queue()
[q.put('flag') for _ in range(50)]
[q2.put(i) for i in range(100)]
tasks = []
c = not q.empty()
c2 = not q2.empty()
while (c or c2) and len(tasks) < 100:
    if c:
        g = q.get()
        tasks.append(g)
        c = not q.empty()
    if c2:
        g2 = q2.get()
        tasks.append(g2)
        c2 = not q2.empty()
    print(len(tasks))
    print(len(tasks) <= 100)
    print(c)
    print(c2)
print('done')
print(tasks)
