#!/bin/bash
cd "$(dirname "$0")"
export DEEPSEEK_API_KEY="sk-498fec10a4c142f8b794c1566ef80a59"
export XHS_COOKIE="abRequestId=bf705d35-e068-54a0-a2f0-c2e85fca72dd; ets=1776503881684; webBuild=6.7.0; xsecappid=xhs-pc-web; a1=19d9fe2001cyw9zgsuvdy9f2aljad3b9r3d7x3qam30000324949; webId=9f22af4a56965493ad4beb2ce2a8ab19; gid=yjfjidJy80FiyjfjidJ88qqAySvEjukM7hfvf06jiJ01VWq80fqDjC888qJ4j4j80qSq0qfy; web_session=040069b6c3163a6b887c3504d63b4b919b5e8c; id_token=VjEAANYD3kHTsXphEzH1vdZKbgnz/JyVCRoWhA5Kk7zQai5NihB3XfA4oC99daAKx1tTmltGQ4Vptx4qRPKR4BPn9h+Y/xEVsLd/ZV0667cP2VjHaZpJZuFxcKcTFgHDED0bnlyY; websectiga=f47eda31ec99545da40c2f731f0630efd2b0959e1dd10d5fedac3dce0bd1e04d; sec_poison_id=36ebfe43-f8da-4ef8-a4f7-1c7a6fde28ca; loadts=1776504962714; unread={%22ub%22:%2269e0f739000000002301ed7a%22%2C%22ue%22:%2269cca6dd000000002103a7ec%22%2C%22uc%22:24}"

# 如果已经在运行就直接打开浏览器
if lsof -i :8888 -t > /dev/null 2>&1; then
  echo "服务已在运行，打开浏览器..."
  open http://localhost:8888
  exit 0
fi

echo "启动医疗AI情报服务..."
.venv/bin/python server.py &
sleep 1
open http://localhost:8888
echo "服务已启动，浏览器将自动打开"
echo "关闭此窗口不会停止服务（后台运行）"
