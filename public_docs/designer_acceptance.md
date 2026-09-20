# 原设计者自主体验：清单与结果模板 / Designer self-directed acceptance

状态 / Status：**原设计者自主体验 = NOT_RUN；独立 T17 = NOT_RUN**。本文是准备说明与记录模板，不是通过报告，
也不代替独立非开发者执行的 T17（见 [researcher_acceptance.md](researcher_acceptance.md)）。

顺序 / Order：先由原设计者在**无逐步指导、维护者不代操作**的条件下完成本清单并确认体验可交付；只有其明确确认后，
才邀请未参与开发的人执行独立 T17。需要帮助的步骤照实记录，不能算通过。

## 维护者准备（一次即可）/ Maintainer preparation

```text
.venv/bin/python tools/phase03_designer_kit.py --prepare
.venv/bin/python tools/phase03_designer_kit.py --verify --root local_data/phase03_20260920/designer_<stamp>
.venv/bin/python tools/phase03_designer_kit.py --serve  --root local_data/phase03_20260920/designer_<stamp>   # 仅 Windows 设计者需要
```

准备工具会**经本实例的真实平台生命周期**取得冻结包并写入 `build/phase03_20260920/<stamp>/`：Web 构建由真实
GUI 上传后从平台存储取回原始字节，macOS arm64 与 Windows x64 完整包由真实登记/上传/批准/授权下载端点取得，
连同 `connection.json`、`artifact_manifest.json`、许可证与第三方声明 sidecar 与 `SHA256SUMS.txt`；每个包内成员
都经过秘密/会话数据扫描，且配置绑定本实例/研究/发行/模式，未重新打包、未手工改写。哈希清单要求与 manifest
成员完整对应（空清单/缺行/重复/陈旧构建或旧源码一律拒绝），必需成员由平台与包内清单派生，删除清单字段不能
绕过。随后新建一个**空**的合成环境 `local_data/phase03_20260920/designer_<stamp>/`：独立数据目录/数据库/密钥、
端口 >= 8040、口令单独 0600、拒绝覆盖或占用受保护目录。设计者只需双击 `START-HERE.command`（由生成的服务
助手完成端口占用/PID/实例身份校验后打开正确地址），不需要输入任何命令；维护者 `--verify` 先做**启动前门槛**
（manifest/哈希/成员扫描/配置与实例·研究·发行·模式绑定/生成引用），任一失败或解析异常都只写出
`NOT_READY` 证据并非 0 返回，不启动服务、不打开浏览器、不运行程序；只有门槛全绿才从停止状态真实
执行 `START-HERE.command` 与 `open-browser.command` 两个入口、核对入口自己记录的打开证据并做真实 Chrome
登录，stop 拒绝/启动失败/身份不符时不会继续打开可疑实例。准备工具还会包含明确标注的示例研究（含真实 Web
发行）用于演示，但自主体验必须允许从零新建研究；种子对象与模式记录在 `manifest.json` 的 `seed_objects` 中。

交付给设计者时必须**同时**给出两个目录：设计者环境目录（含 `START-HERE.command`）与冻结包目录（含
`OPEN-ADMIN-WINDOWS.cmd` 与包本体）；两者都在本机项目根目录下、都不提交。

Windows 设计者：维护者先执行一次 `--serve`（工程侧作用域反向隧道，不修改 DNS/防火墙/证书信任，也不需要设计者
输入命令），然后在 Windows 上双击 `OPEN-ADMIN-WINDOWS.cmd` 打开后台。Windows 完整包实机运行 WN01–WN06 仍需
真实 Windows x64 电脑与工程侧隧道；缺失时保持未运行。

## 体验清单（设计者可自由调整顺序）/ Checklist

- [ ] 登录本地后台（地址以双击后打开的页面为准，端口 >= 8040），切换中英双语。
- [ ] 浏览示例研究：三种参与模式、名单导入（CSV，前导零与引号）、构建与发行、招募状态、当前发行。
- [ ] 从零新建一个自己的研究：模式 → 名单 → 构建/上传 → 批准发行 → 设置当前发行 → 打开招募。
- [ ] 用 Web 入口真实参与：开始、两个试次、断网继续、恢复、完成，检查提示是否清楚。
- [ ] 用 macOS 完整包真实运行一次；Windows 设计者使用 Windows 完整包（WN01–WN06 需要真实 Windows x64）。
- [ ] 查看状态/会话/名单查询与分页、三种导出（JSONL 固定快照 / CSV / metadata）、权限与账号页面。
- [ ] 记录任何需要猜测、犹豫或需要维护者解释的地方。

## 边界 / Boundaries

- 只使用准备工具创建的隔离实例与合成数据；**不要**连接旧验收库（`local_data/gep.sqlite3`、
  `local_data/independent_acceptance_20260912`）或 8000 端口的开发实例。
- 不修改产品边界、研究协议、评分、排除、同意、数据用途、身份/委派边界或部署配置。
- 未完成的项目保持"未运行"；需要帮助的步骤照实写，不能改写成通过。

## 结构化结果模板 / Structured results template

| 项目 | 结果（通过 / 需要帮助 / 失败 / 未运行） | 证据或原话 |
|---|---|---|
| 登录与语言切换 | 未运行 | |
| 示例研究浏览 | 未运行 | |
| 从零新建研究（模式/名单/构建/发行/当前发行/招募） | 未运行 | |
| Web 真实参与（两试次、断网、恢复、完成） | 未运行 | |
| macOS 完整包真实运行 | 未运行 | |
| Windows 完整包真实运行（WN01–WN06） | 未运行 | |
| 状态/会话/名单查询与分页 | 未运行 | |
| 三种导出（JSONL/CSV/metadata） | 未运行 | |
| 权限与账号页面 | 未运行 | |
| 主观体验（是否愿意交给他人使用） | 未运行 | |

- 体验日期 / 日期：未运行
- 使用的包与摘要前缀：未运行
- 需要维护者解释的步骤：未运行
- 结论：**未运行**（只有设计者本人勾选"可以交给独立 T17"后，才邀请外部测试者）

## 已知限制 / Known limitations

- 本地被篡改的 PCK 不会被程序运行时自动拒绝：运行时不做包完整性校验。
- 包的哈希清单只是传输完整性，不是签名；Windows 包未做代码签名，SmartScreen/杀毒提示按实记录。
- Windows 实机 WN01–WN06 需要真实 Windows x64 电脑；缺失时保持未运行，不能用 macOS、交叉编译或浏览器结果替代。
