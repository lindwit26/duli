import threading, copy, ujson

class SharedDict(dict):
    def __init__(self, val = {}):
        self.lock = threading.Lock()
        self.d = val

    def add(self, key, val):
        self.lock.acquire()
        try:
            self.d[key] = val
        finally:
            self.lock.release()

    def add_if_new(self, key, val):
        self.lock.acquire()
        try:
            if key not in self.d:
                self.d[key] = val
        finally:
            self.lock.release()

    def remove(self, key):
        with self.lock:
            try:
                return self.d.pop(key)
            except:
                return None

#    def del(self, key):
#        with self.lock:
#            if key in self.d:
#                del self.d[key]

    def keys(self):
        with self.lock:
            return list(self.d.keys())

    def values(self):
        with self.lock:
            return list(self.d.values())

    def length(self):
        with self.lock:
            return len(self.d)

    def get(self, key):
        with self.lock:
            try:
                return self.d[key]
            except:
                return None

    def plus(self, key, num):
        with self.lock:
            try:
                self.d[key] += num
                return True
            except:
                return False

    def has(self, key):
        with self.lock:
            return key in self.d

    def copy(self):
        return ujson.loads(ujson.dumps(self.d))

    def __iter__(self):
        return self.d

class SharedList(list):

    def __init__(self, val = []):
        self.lock = threading.Lock()
        self.l = val

    def append(self, val):
        with self.lock:
            try:
                self.l.append(val)
                return True
            except:
                return False
    def remove(self, val):
        with self.lock:
            try:
                self.l.remove(val)
                return True
            except:
                return False

    def contains(self, val):
        with self.lock:
            return val in self.l

    def get(self, index):
        with self.lock:
            try:
                return self.l[index]
            except:
                return None

    def set(self, index, val):
        with self.lock:
            try:
                self.l[index] = val
                return True
            except:
                return False

class SharedSet(set):
    def __init__(self, val = set()):
        self.lock = threading.Lock()
        self.s = val

    def add(self, v):
        with self.lock:
            self.s.add(v)

    def remove(self, v):
        with self.lock:
            try:
                self.s.remove(v)
                return True
            except:
                return False

    def has(self, key):
        with self.lock:
            return key in self.s

    def __iter__(self):
        return copy.deepcopy(self.s)
