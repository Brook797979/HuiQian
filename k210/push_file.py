# -*- coding: utf-8 -*-
import serial, sys, time, base64, os

PORT = 'COM7'
BAUD = 115200

def drain(ser, seconds):
    end = time.time() + seconds; buf = b''
    while time.time() < end:
        n = ser.in_waiting
        if n: buf += ser.read(n)
        else: time.sleep(0.05)
    return buf

def wait_marker(ser, marker, timeout=8):
    end = time.time() + timeout; buf = b''
    while time.time() < end:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            if marker in buf: return buf
        else: time.sleep(0.03)
    return buf

def raw_exec(ser, code, timeout=60):
    for i in range(0, len(code), 512):
        ser.write(code[i:i+512].encode('utf-8'))
        time.sleep(0.015)
    ser.write(b'\x04')
    return drain(ser, timeout)

def push_file(ser, local, remote):
    raw = open(local, 'rb').read()
    b64 = base64.b64encode(raw).decode()
    CH = 2048
    parts = [b64[i:i+CH] for i in range(0, len(b64), CH)]
    lines = ["import ubinascii, gc", "f=open('%s','wb')" % remote]
    for i, p in enumerate(parts):
        lines.append("f.write(ubinascii.a2b_base64('%s'))" % p)
        if i % 4 == 3:
            lines.append("gc.collect()")
    lines += ["f.close()", "import os", "print('SIZE', os.stat('%s')[6])" % remote, "print('DONE')"]
    code = "\n".join(lines) + "\n"
    out = raw_exec(ser, code, timeout=90)
    txt = out.decode('utf-8','replace')
    ok = ('SIZE %d' % len(raw)) in txt and 'DONE' in txt
    print('PUSH %s -> %s: %s (%d bytes)' % (os.path.basename(local), remote, 'OK' if ok else 'FAIL', len(raw)))
    if not ok:
        print(txt[-300:])
    return ok

def main():
    pairs = sys.argv[1:]
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b'\x03'); time.sleep(0.2); ser.write(b'\x03'); time.sleep(0.2)
    ser.reset_input_buffer()
    ser.write(b'\x01')
    wait_marker(ser, b'raw REPL', 5)
    ok_all = True
    for i in range(0, len(pairs), 2):
        local, remote = pairs[i], pairs[i+1]
        if not push_file(ser, local, remote):
            ok_all = False
    ser.write(b'\x02')
    time.sleep(0.2)
    ser.write(b'\x03'); time.sleep(0.2)
    ser.write(b'import machine\r\nmachine.reset()\r\n')
    time.sleep(3)
    drain(ser, 4)
    print('已重启')
    ser.close()
    sys.exit(0 if ok_all else 1)

if __name__ == '__main__':
    main()