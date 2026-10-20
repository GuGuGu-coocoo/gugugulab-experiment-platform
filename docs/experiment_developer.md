# GEC 接口参考

首次接入请读[中文开发者教程](gec_godot.md)或 [English developer guide](gec_godot_en.md)。这里补充数据接口的边界，不重复安装和导出步骤。协议细节见 [gep/1](protocol.md)。

## 便利层与低层

`addons/gec/session.gd` 通过 `configure_defaults`、`prepare`、`record`、`finish`、`status` 管理排队和串行本地提交。`record()` 返回入队状态；`local_saved` 和 `completed` 分别是本地提交与本地完成信号，不是远端 ACK。

低层 `addons/gec/experiment_data.gd` 保留 `prepare`、`record`、`commit`、`checkpoint`、`finish` 和 `status`。只有需要自行控制事务／checkpoint 时才直接使用；不要让它与未排空的便利层并发写同一会话。

原来的四参数 `record(kind, payload, schema, observed)` 仍可用。默认字段薄封装允许先配置，再简短记录：

```gdscript
data.configure_defaults({
    "event_type": "task.response",
    "schema": {"id": "response", "version": "1"},
    "fields": {
        "trial_id": func(): return trial.id,
        "choice": func(): return trial.choice
    }
})
var result = await data.record()
```

这里的 `trial` 是宿主定义的对象，不是 GEC 提供的任务节点。声明字段必须与实际 schema 一致。

## 字段与复制

`fields` 是字段名到 Callable 的映射，与返回 Dictionary 的 `snapshot_provider` 互斥。只读取显式声明的字段，不遍历场景树、窗口或未声明的身份信息。

显式 `kind`／`schema` 覆盖默认值；非 null 的 `payload` 完全替代默认 provider，不隐式合并。复制发生在调用 `record()` 时、异步等待之前，之后修改原对象不会改写历史事件。

只接受可序列化的普通有限 JSON 值。循环对象或超过本地深度限制 64 会被拒绝；合法共享但无环的结构不因此拒绝。服务端的 16 层限制独立生效，宿主不能因为本地接受就假定服务端一定接受。

`record()` 不自动等同于 `commit()` 或 `finish()`，也不替你定义 trial 边界。来源时钟、随机化、评分、排除和科学顺序都由任务负责。

## 完成和故障

本地事务成功才可声称已保存；收到并在本地提交有效 ACK 后，才可减少 pending 集合。HTTP 200 不是没有缺失数据的充分条件。

暂时网络错误按原标识重传并退避，保留 Retry-After；永久授权拒绝或研究删除不会为了凑次数继续重发。正常发送失败后，连续三轮真正执行且没有有效 ACK 进展的补传失败才显示相应失败导出。仅等待、检测离线或三个分片不算三轮失败。

结束页先显示任务结束和真实保存／上传状态。失败数据导出只针对设备上合法、未锁定且未清理的记录，不清空队列，不包含 token、proof 或密码。研究删除后旧客户端停止向该研究发送，合法本地导出与后台恢复是不同能力。

## 检查点与恢复

0.1.0 不公开恢复码／恢复许可入口。底层兼容数据和保存机制保留，但不应在新接线中承诺一个尚未提供的恢复界面。

需要 checkpoint 时，任务先定义完整 trial 边界、下一 trial、随机状态及兼容版本。没有这些声明时只可处理已保存数据，不推断继续任务的位置、不补跑已完成 trial。跨版本配置不能改写旧 pending 的研究、release、session 或 event ID。

## 配置与分发

Web 由 GEP 在隔离预览或参与入口注入公开配置；原生从平台下载的完整发行包自带程序同级 `connection.json`。它不是账户密码文件。SDK／协议版本和 `purpose: synthetic` 沿用现有兼容契约，不手工改旧配置来切换研究。

实验 Web 包与后台不共用管理 Cookie，但多个实验仍共用 experiment origin。只上传你有权发布且信任的程序，不把隔离预览误解为任意不可信代码的安全沙箱。
