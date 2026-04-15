# 接入微信公众号：wewe-rss 配置指南

wewe-rss 利用微信读书的接口把公众号文章转成 RSS，不需要扫码登录微信，
相对稳定。项目地址：https://github.com/cooderl/wewe-rss

## 第一步：部署 wewe-rss

最简单的方式是用 Docker：

```bash
docker run -d \
  --name wewe-rss \
  -p 4000:4000 \
  -e DATABASE_TYPE=sqlite \
  -v $(pwd)/wewe-data:/app/data \
  cooderl/wewe-rss
```

启动后访问 http://localhost:4000，用微信扫码登录微信读书完成授权。

## 第二步：订阅公众号

在 wewe-rss 界面搜索并关注你想监控的公众号，比如「健康界」「丁香园」。
订阅后每个公众号会生成一个 fakeid，RSS 地址格式为：
  http://localhost:4000/feeds/<fakeid>.atom

在界面点击公众号可以看到完整的 RSS 地址。

## 第三步：填入 config.yaml

```yaml
wewe_rss:
  enabled: true
  base_url: "http://localhost:4000"
  accounts:
    - name: "健康界"
      feed_path: "/feeds/MjM5NTAxMjIwNA==.atom"
      tags: ["医疗", "行业新闻"]
    - name: "丁香园"
      feed_path: "/feeds/MzI2Mzc1MTA4Mw==.atom"
      tags: ["医疗", "临床"]
```

## 第四步：运行抓取

```bash
.venv/bin/python main.py fetch
```

会同时抓取外部RSS源和所有配置的公众号，新文章自动入库。

## 注意事项

- wewe-rss 依赖微信读书授权，token 有有效期，过期需要重新扫码
- 文章抓取有延迟，通常比发布晚数小时到一天
- 只能抓到订阅后的新文章，历史文章需要手动导入
