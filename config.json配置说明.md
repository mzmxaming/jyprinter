# config.json 配置说明

> 适用版本：**v1.4.1 及以上**（网络打印服务 jyprinter）
> 本文件面向使用者与运维，逐项说明 `config.json` 的每个配置项；程序侧的权威定义在 `core/settings.py` 的 `DEFAULT_CONFIG`，两者保持一致。
>
> 作者：涯系石头   微信公众号：涯系石头

---

## 一、文件在哪里

| 运行方式 | `config.json` 位置 |
| --- | --- |
| Windows 免安装版（exe） | 与 `jyprinter.exe` **同级目录**，例如 `D:\jyprinter\config.json` |
| 麒麟 / Linux 免安装版 | 安装目录（默认 `/opt/jyprinter/config.json`） |
| 麒麟 / Linux 系统包（deb / rpm） | `/opt/jyprinter/config.json`（程序目录 root 只读，**仅此文件归服务用户可写**） |

> 注意：**配置目录与数据目录是分开的**——可写数据（上传文件、预览、日志、账号）在程序目录下的 `data/`；deb/rpm 由环境变量 `JYPRINTER_DATA_DIR` 指到 `/var/lib/jyprinter`，配置仍在 `/opt/jyprinter`。

首次运行时程序会自动生成一份含全部默认值的 `config.json`，可直接编辑。

---

## 二、怎么改、什么时候生效

1. **改完必须重启程序**。配置在启动时读取一次并缓存在内存中，运行期间不会重新读取文件：
   - Windows：托盘图标右键 →「退出」，再重新打开程序
   - 麒麟 / Linux（systemd）：`sudo systemctl restart jyprinter`
2. **格式要求**（标准 JSON，注释、单引号都不允许）：
   - 键名与字符串值必须用**双引号**；各项之间用**逗号**分隔，最后一项**不加**逗号
   - 布尔值必须写 `true` / `false`，**不要写成字符串 `"false"`** —— 非空字符串会被当作「真」
3. **未知键会被忽略**：程序只认识下表列出的键；拼错的键不报错也不生效，并会在程序下次写回配置时被丢弃
4. **命令行参数**：`--port 8080`、`--host 192.168.1.10` 会**写回配置文件**（持久化）；`--no-tray`、`--hide-console` 只是本次运行的开关，不写入

---

## 三、全部配置项

### 网络与启动

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `host` | 字符串 | `"0.0.0.0"` | 监听地址。`0.0.0.0` = 允许局域网访问（推荐）；改成 `127.0.0.1` 则**只有本机能打开页面**，别的电脑/手机就连不上了 |
| `port` | 整数 | `5000` | 网页服务端口。改了之后访问地址随之变化：`http://<本机IP>:<新端口>`；端口被占用时程序会启动失败并在日志中提示 |
| `hide_console_on_start` | 布尔 | `true` | 托盘模式下启动后自动隐藏控制台窗口。**仅对「带控制台」的 Windows 构建有意义**：v1.3.6 起的发布版本身就是无控制台程序，该项已无实际作用（源码运行想在终端里隐藏窗口，请加 `--hide-console`） |

### 默认打印参数（页面打开时的预填值）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `printer` | 字符串 | `""` | 默认打印机名。**留空 = 跟随系统默认打印机**；填写时必须与系统里的打印队列名完全一致（Windows：「设置 → 打印机和扫描仪」里的名字；麒麟/Linux：`lpstat -p` 显示的名字） |
| `copies` | 整数 | `1` | 默认份数（页面上可临时改，范围 1~99） |
| `duplex` | 字符串 | `"simplex"` | `simplex` 单面 / `vertical` 双面（长边翻转） |
| `paper` | 字符串 | `"A4"` | 默认纸张，如 `A4` `A5` `B5` `Letter` `Legal` `A3` `16K` |
| `scale` | 字符串 | `"fit"` | 默认缩放方式：`fit` 适应页面 / `actual` 实际大小 / `custom` 自定义百分比 |
| `scale_percent` | 整数 | `100` | `scale = "custom"` 时使用的缩放百分比（页面限制 10~400） |
| `color` | 字符串 | `"color"` | `color` 彩色 / `mono` 黑白（黑白会先在位图层转灰度，效果与驱动无关） |
| `quality` | 字符串 | `"normal"` | 打印质量：`draft` 草稿 150 DPI / `normal` 标准 300 DPI / `high` 高清 600 DPI |
| `orientation` | 字符串 | `"auto"` | 出纸方向：`auto` 跟随文档首页方向 / `portrait` 纵向 / `landscape` 横向 |
| `fit_table` | 布尔 | `true` | 表格类文件（`xls`/`xlsx`/`et`）转换前设为「适应一页宽」。**仅 Windows 生效**（Linux 由 LibreOffice 转换，需在文件里自行设置）；页面上可临时勾选 |

> 这些值只是**默认值**：上传页面的表单每次打开都会按此预填，用户可当场修改；改配置不会影响已经在队列里的任务。

### 流程与队列

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `require_confirmation` | 布尔 | `false` | `true` = 上传后不立即打印，任务先进入「待确认」，预览确认后才打印；页面上可临时勾选 |
| `task_timeout_seconds` | 整数 | `180` | 单个任务等待「系统打印队列回落到提交前水平」的上限（秒）。超时判定为**失败**，失败原因会带上当时的打印机状态；页数多/份数多的文档可适当调大 |

### 文件、日志与限额

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `file_ttl_minutes` | 整数 | `30` | 上传文件的保留时间（分钟）。仅用于**失败或遗留**文件的定时回收：打印成功与取消都会**立即删除**文件（见下一项），不受此值影响 |
| `keep_file_after_print` | 布尔 | `false` | v1.4.1 新增。`false` = 打印成功后**立即删除**源文件与预览缓存（默认，隐私优先）；`true` = **保留**，任务列表对已完成任务显示「预览」与「重打」，且这些文件**不参与定时清理**（需自行清理，见 FAQ 第 6 条） |
| `log_keep_days` | 整数 | `7` | 打印/登录日志的保留天数，由维护线程每天清理超期记录 |
| `max_upload_mb` | 整数 | `100` | 上传大小上限（MB）。注意是**单次请求**的上限：一次选多个文件时按**总和**计算，超限返回「文件超过上传上限（N MB）」 |

### 界面

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `filter_virtual_printers` | 布尔 | `true` | 网页端打印机下拉列表里隐藏虚拟打印机（如「Microsoft Print to PDF」）。只影响展示，不影响打印；需要用它做无纸验证时可置 `false` |

---

## 四、完整示例（当前默认值）

```json
{
  "host": "0.0.0.0",
  "port": 5000,
  "printer": "",
  "copies": 1,
  "duplex": "simplex",
  "paper": "A4",
  "scale": "fit",
  "scale_percent": 100,
  "color": "color",
  "quality": "normal",
  "orientation": "auto",
  "fit_table": true,
  "filter_virtual_printers": true,
  "require_confirmation": false,
  "hide_console_on_start": true,
  "task_timeout_seconds": 180,
  "file_ttl_minutes": 30,
  "keep_file_after_print": false,
  "log_keep_days": 7,
  "max_upload_mb": 100
}
```

常用修改示例（注意：**JSON 里不能写注释**，下面各行含义见表后的说明）：

```json
{
  "port": 8080,
  "printer": "Lenovo LJ2200",
  "duplex": "vertical",
  "require_confirmation": true,
  "max_upload_mb": 300,
  "keep_file_after_print": true
}
```

| 上面这一行 | 含义 |
| --- | --- |
| `"port": 8080` | 网页端口改为 8080（记得重启，访问地址同步变成 `http://<本机IP>:8080`） |
| `"printer": "Lenovo LJ2200"` | 固定使用这台打印机，不跟随系统默认打印机 |
| `"duplex": "vertical"` | 默认双面（长边翻转） |
| `"require_confirmation": true` | 上传后先预览确认，再打印 |
| `"max_upload_mb": 300` | 单次上传上限放宽到 300 MB |
| `"keep_file_after_print": true` | 打印成功后保留文件，界面上可「重打」 |

> 这是**片段示意**：实际文件里只写要改的几项也能用（其余键会用默认值补齐），但请保持 JSON 语法正确、不加注释；程序下次写回配置时会补全为完整的一份。

---

## 五、常见问题

1. **为什么没有 `page_range`（页码默认值）？**
   页码已在 v1.3.4 移除。页码范围是一次性的打印意图，做成持久默认值会导致「每次打开页面都被自动带上、没注意就只打了第 1 页」。需要打部分页时在页面里临时填写。

2. **改完配置为什么没生效？**
   配置只在程序启动时读取一次。请重启程序（Windows：托盘右键「退出」后重开；麒麟/Linux：`sudo systemctl restart jyprinter`）。

3. **改了端口后网页打不开？**
   访问地址要跟着改成 `http://<本机IP>:<新端口>`；同时确认防火墙已放行该端口（Windows 可用 `tools\fix_tool.py` 检测并放行）。

4. **布尔值写成 `"false"` 会怎样？**
   会被当成「真」——程序以「非空即真」判断。请写 JSON 布尔值 `false`，不要加引号。

5. **哪些情况程序会自动改写 config.json？**
   只有两处：① 启动时带 `--host` / `--port` 参数；② 托盘菜单「修改服务端口」。其余项目只有手工编辑才会变化。

6. **开启了 `keep_file_after_print` 之后，文件会一直留着吗？**
   是的：打印成功的文件与预览缓存会保留且**不参与定时清理**（否则「重打」会在 `file_ttl_minutes` 到期后失效）。需要清理时有两种方式：
   - 把 `keep_file_after_print` 改回 `false` 并重启——之后的定时巡检会把这些文件回收；
   - 停掉程序后删除 `data\uploads` 与 `data\previews` 下的内容。

7. **删掉 config.json 会怎样？**
   下次启动会按默认值重新生成一份（端口回到 5000、打印参数回到默认）。`data/` 里的账号、日志、上传文件不受影响。

8. **麒麟/Linux 上手工编辑要注意什么？**
   用 `sudo nano /opt/jyprinter/config.json` 编辑后，确认文件属主仍是服务用户，否则服务无法写回配置：

   ```bash
   sudo chown jyprinter:jyprinter /opt/jyprinter/config.json
   sudo systemctl restart jyprinter
   ```

9. **网页端会读取哪些配置？**
   页面通过 `GET /api/config` 读取一部分用于表单预填：`printer`、`copies`、`duplex`、`paper`、`require_confirmation`、`filter_virtual_printers`；其余项通过页面模板注入或仅在服务端生效。网页端目前**没有**配置编辑界面，全部配置请直接编辑 `config.json`。

---

## 
