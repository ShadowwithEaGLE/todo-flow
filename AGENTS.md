# Flow Board 部署入口（给 Agent）

收到部署请求并拿到本链接时，先按下表确定目标，再执行启动和对应验收。没有指定目标时，默认启动**本机个人预览**。若只要求试用现有在线版，直接返回在线地址即可。

| 目标 | 路径 |
| --- | --- |
| 本机试用或未指定目标 | 使用「本机个人预览」 |
| 发布公开的个人版 | 使用「静态托管」 |
| 让成员共用数据 | 使用「可信局域网共享」 |

已有工作区时先确认仓库远端和工作区状态，保留现有改动与数据。全新环境可以用以下命令取代码，后续命令在仓库目录运行。

```shell
git clone https://github.com/ShadowwithEaGLE/todo-flow.git
cd todo-flow
```

## 本机个人预览

用浏览器直接打开仓库内的 `todo-flow.html` 即可离线使用。需要返回 HTTP 地址时，使用 Python 3 将该文件所在目录绑定到本机回环地址。端口冲突时选用一个空闲端口，并更新返回的 URL。

Windows PowerShell：

```powershell
py -3 -m http.server 8000 --bind 127.0.0.1
```

macOS / Linux：

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

在宿主机访问 [本机预览](http://127.0.0.1:8000/todo-flow.html)。确认页面返回 HTTP 200，且页面包含 `FLOW_BOARD_SERVER = false`。在有浏览器的情况下，用空白测试环境新增一条任务并刷新确认保留。需要用户继续访问时，保持本机服务运行，或用目标环境已有的进程管理工具托管它。

## 静态托管

已发布的个人版可直接使用：[在线体验](https://shadowwitheagle.github.io/todo-flow/todo-flow.html)。

部署自己的副本时，把 `todo-flow.html` 和 `version.json` 放在同一个静态发布目录，直接发布文件即可。用 GitHub Pages 时，可将这两个文件提交到目标仓库的独立发布分支，再在 **Settings → Pages** 选择 **Deploy from a branch**，选中发布分支及 **/ (root)**。等部署完成后访问 Pages 地址下的 `todo-flow.html`，以同样方式验收个人模式。发布到哪个账号或项目由部署请求决定。

公开目录仅放这两个静态文件。页面数据保存在当前浏览器，各访客拥有自己的数据。当前页面的更新检查指向上游 GitHub Pages，独立维护的副本如需自己的更新通道，应另行配置。

## 可信局域网共享

使用部署机的可信内网 IPv4 和 Python 3.10+。下面以 `192.168.1.25` 为例，执行前替换为部署机实际地址。Windows 的网卡名为 `WLAN` 时，也可直接用 `start-flow-board.cmd`。

Windows PowerShell：

```powershell
py -3 server.py --host 192.168.1.25 --port 8080
```

macOS / Linux：

```sh
python3 server.py --host 192.168.1.25 --port 8080
```

1. 先在部署机请求 `http://192.168.1.25:8080/todo-flow.html`。确认 HTTP 200，且页面包含 `FLOW_BOARD_SERVER = true`。
2. 从同一可信局域网的另一台设备打开该地址，输入显示名称，确认能进入共享看板。新实例可用测试任务确认两个会话能看到同一份数据。
3. 数据库创建在 `data/flow-board.sqlite3`。已有部署按 [局域网手册](LAN_SETUP.md) 备份，避免用测试数据改动现有共享库。长期运行时保持服务进程在线。

该服务对网络可达成员授予完整读写权限，显示名称仅用于活动记录。只向可信网段开放所需端口。公网需求可走以上静态个人版，共享服务限定在可信局域网。

完成后回报部署模式、准确 URL、进程状态、数据位置及实际验收结果。若从远程环境只能验证回环地址，明确说明该地址仅在该环境可达。
