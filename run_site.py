"""One process and CPU budget for the selected UI and companion port."""
import argparse
import os
import socket
import uvicorn


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8102)
    parser.add_argument("--compat-port",type=int,default=8000)
    parser.add_argument("--application",choices=("production_app:app","app:app"),default="production_app:app")
    args=parser.parse_args()
    ports=[args.port]+([args.compat_port] if args.compat_port and args.compat_port!=args.port else [])
    if any(port<1 or port>65535 for port in ports):
        parser.error("端口必须在 1 到 65535 之间")
    sockets=[]
    try:
        for port in ports:
            sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            sockets.append(sock)
            if os.name=="nt":
                sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            else:
                sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.bind(("127.0.0.1",port))
            sock.listen(2048)
        config=uvicorn.Config(args.application,workers=1,
            proxy_headers=bool(os.environ.get("GGAB_PUBLIC_ORIGIN")),
            forwarded_allow_ips="127.0.0.1,::1",limit_concurrency=64,timeout_keep_alive=5)
        print("网站入口："+", ".join(f"http://127.0.0.1:{port}" for port in ports),flush=True)
        uvicorn.Server(config).run(sockets=sockets)
    finally:
        for sock in sockets:
            sock.close()


if __name__=="__main__":
    main()
