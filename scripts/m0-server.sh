#!/usr/bin/env bash
# beeOS M0 demo server 启停脚本
# 用法：bash scripts/m0-server.sh {start|stop|status|restart}
#
# 跟 deploy-m0.sh 配合使用：部署完后跑 `bash scripts/m0-server.sh start` 起 server
# 注意：M0 不接 systemd（无 Queen/Hive），用 nohup 在后台跑

set -euo pipefail

REMOTE="${REMOTE:-root@101.37.146.194}"
REMOTE_DIR="${REMOTE_DIR:-/opt/beeos}"
PORT="${PORT:-8085}"
PIDFILE="/tmp/beeos-m0-server.pid"
LOGFILE="/var/log/beeos-m0.log"

run_ssh() {
  ssh "$REMOTE" "$@"
}

cmd_start() {
  echo "==> 启动 M0 server (port $PORT)"
  run_ssh "
    set -e
    if [ -f $PIDFILE ] && kill -0 \$(cat $PIDFILE) 2>/dev/null; then
      echo '   已在运行 (pid '\$(cat $PIDFILE)')'
      exit 0
    fi
    cd $REMOTE_DIR
    nohup ./venv/bin/uvicorn bee.server:app \\
      --host 0.0.0.0 --port $PORT \\
      > $LOGFILE 2>&1 &
    echo \$! > $PIDFILE
    sleep 1
    echo '   started (pid '\$(cat $PIDFILE)')'
  "
  echo "    日志: ssh $REMOTE 'tail -f $LOGFILE'"
  echo "    访问: http://$REMOTE_HOST:8085/  (把 REMOTE 拆出 host)"
}

cmd_stop() {
  echo "==> 停止 M0 server"
  run_ssh "
    if [ ! -f $PIDFILE ]; then
      echo '   未运行'
      exit 0
    fi
    PID=\$(cat $PIDFILE)
    if kill -0 \$PID 2>/dev/null; then
      kill \$PID
      sleep 1
      echo '   stopped (was pid '\$PID')'
    else
      echo '   进程已不存在'
    fi
    rm -f $PIDFILE
  "
}

cmd_status() {
  run_ssh "
    if [ -f $PIDFILE ] && kill -0 \$(cat $PIDFILE) 2>/dev/null; then
      echo 'M0 server: RUNNING (pid '\$(cat $PIDFILE)')'
      curl -fsS http://127.0.0.1:$PORT/health 2>/dev/null || echo '   health check failed'
    else
      echo 'M0 server: STOPPED'
    fi
  "
}

cmd_restart() {
  cmd_stop
  cmd_start
}

case "${1:-status}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  restart) cmd_restart ;;
  *)
    echo "Usage: $0 {start|stop|status|restart}"
    exit 1
    ;;
esac
