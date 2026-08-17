"""AS608 指纹模块驱动 (树莓派直连, 57600, pyserial)"""
import serial, time


class AS608:
    def __init__(self, port, baud=57600, timeout=1):
        self.ser = serial.Serial(port, baud, timeout=timeout)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _send(self, pid, data=b''):
        pkt = bytes([0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, pid,
                     (len(data) >> 8) & 0xFF, len(data) & 0xFF]) + data
        chk = pid + len(data)
        for b in data:
            chk += b
        pkt += bytes([(chk >> 8) & 0xFF, chk & 0xFF])
        self.ser.reset_input_buffer()
        self.ser.write(pkt)

    def _recv(self, timeout=2):
        self.ser.timeout = timeout
        # 读到 EF 01 开头
        for _ in range(3):
            hdr = self.ser.read(1)
            if not hdr:
                return None
            if hdr[0] == 0xEF:
                rest = self.ser.read(8)
                if len(rest) < 8:
                    return None
                hdr += rest
                break
        else:
            return None
        ln = (hdr[7] << 8) | hdr[8]
        if ln < 3 or ln > 64:
            return None
        body = self.ser.read(ln)
        if len(body) < ln:
            return None
        return body  # body[0]=confirm, 后为参数(最后2字节校验和)

    def _cmd(self, pid, data=b'', timeout=2):
        self._send(pid, data)
        r = self._recv(timeout)
        if r is None:
            return -1, None
        return r[0], r[1:]

    def verify_password(self):
        c, _ = self._cmd(0x13, bytes([0xFF, 0xFF, 0xFF, 0xFF]))
        return c == 0

    def get_image(self, timeout=2):
        c, _ = self._cmd(0x01, timeout=timeout)
        return c  # 0=有手指, 2=无手指

    def img2tz(self, buf=1):
        c, _ = self._cmd(0x02, bytes([buf]))
        return c

    def search(self, buf=1, start=0, count=160):
        data = bytes([buf, (start >> 8) & 0xFF, start & 0xFF, (count >> 8) & 0xFF, count & 0xFF])
        c, p = self._cmd(0x04, data)
        if c == 0 and len(p) >= 4:
            return 0, (p[0] << 8) | p[1]
        return c, None

    def regmodel(self):
        c, _ = self._cmd(0x05)
        return c

    def store(self, buf=1, page=0):
        c, _ = self._cmd(0x06, bytes([buf, (page >> 8) & 0xFF, page & 0xFF]))
        return c

    def _wait_finger(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            c = self.get_image(timeout=1)
            if c == 0:
                return 0
            if c != 2:
                return c
        return 2

    def verify(self, timeout=10):
        """等手指按下并搜索; 返回 (0, fp_id) 或 (err, None)"""
        c = self._wait_finger(timeout)
        if c != 0:
            return c, None
        c = self.img2tz(1)
        if c != 0:
            return c, None
        return self.search()

    def enroll(self, slot, timeout=15, on_first=None, on_again=None):
        """录指纹到槽位 slot; 返回 (0, slot) 或 (err, None)
        on_first: 第一次按下并成像后回调; on_again: 第二次按下前回调(提示移开再按)
        """
        c = self._wait_finger(timeout)
        if c != 0:
            return c, None
        c = self.img2tz(1)
        if c != 0:
            return c, None
        if on_first:
            on_first()
        c = self._wait_finger(timeout)
        if c != 0:
            return c, None
        if on_again:
            on_again()
        c = self.img2tz(2)
        if c != 0:
            return c, None
        c = self.regmodel()
        if c != 0:
            return c, None
        c = self.store(1, slot)
        if c != 0:
            return c, None
        return 0, slot