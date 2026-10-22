# A100 ROI 占用状态的时间防抖与失联处理

作者：Keming Yang（GitHub: [youngkermit8-coder](https://github.com/youngkermit8-coder)）。本项目为独立Community Project，仅新增本目录，遵循仓库Apache-2.0许可证。

## 数据来源与复现环境

使用现有社区项目 [a100-indoor-cart-mti](../a100-indoor-cart-mti/README.md) 的公开A100采集文件：

`../a100-indoor-cart-mti/data/targets/2026_08_02_16_19_35_Tatget_CTLRR-300-V2_1_RawTarget.csv`

该项目标明数据于2026-08-02采集，原采集与场景说明归原贡献者所有。本项目未采集新数据、不复制大文件、不重新声称数据所有权。基线提交为`9a41e22b22b981d1500e4a28e056407baeb1027f`；输入SHA-256记录在结果中。

文件按START/END解析，重复设备帧号保留文件顺序；检查非有限测量、非法ID及负距离，保存不完整帧标记。默认日志207帧、3456条记录，拒绝行和不完整帧均为0。读取器只面向本实验的标记式日志，不替代官方通用解析器。`range`按米、`angle`按度，几何采用官方Python示例的余弦/正弦约定；没有安装姿态或物理真值校准。

本次环境：Windows、Python 3.11.15、NumPy 2.4.6、Matplotlib 3.11.2。不需要连接雷达或付费软件，原采集固件与工具精确版本未知。**合成真值、注入噪声和公开采集数据明确分开；结果不构成产品性能承诺。**

## 运行

在仓库根目录执行：

```sh
python -m pip install -r community/projects/a100-occupancy-hysteresis/requirements.txt
python -m unittest discover -s community/projects/a100-occupancy-hysteresis/tests -v
python community/projects/a100-occupancy-hysteresis/src/study.py
```

入口支持`--input`和`--output`。默认输入相对脚本定位，与工作目录无关。固定实验种子为20261022；默认输出为本项目results目录，会覆盖同名实验产物。算法参数暴露在src/study.py函数接口中；CLI仅选择数据与输出目录，实验默认参数在结果JSON中列出。

结果：[summary.json](results/summary.json)。源码：[study.py](src/study.py)、[logio.py](src/logio.py)。

![实验结果](results/comparison.png)

## 问题与方法

逐帧点数阈值容易因孤立噪点、单帧漏点频繁跳变；固定帧数防抖又依赖采样率。本项目使用真实采集时间的持续时长确认状态，并把未知状态作为一等输出。

默认进入条件：ROI点数>=2持续0.3秒；退出条件：点数<=0持续0.7秒。中间点数保持已有状态，但打断正在确认的持续时间。超过0.25秒没有相邻有效观测、时间重复/倒退、未闭合帧或缺失时间都重置为unknown；恢复后重新积累证据。只在update收到新输入时推进状态，不提供后台时钟或实时看门狗。

ROI用示例坐标 `X=r*cos(angle),Y=r*sin(angle)` 定义：X在[1,5] m、Y在[-2,2] m。原始ObjId不被当作跨帧身份，也不做人数/车辆数估计。

## 实际运行结果与取舍

合成序列长20秒、采样间隔0.1秒，5~12秒为占用，包含孤立误报、短漏报及8~8.5秒缺测。

| 指标 | 结果 |
|---|---:|
| 瞬时阈值跳变次数（缺测按false计，仅作朴素基线） | 16 |
| 防抖已知状态之间跳变次数 | 2 |
| 防抖已知状态覆盖率 | 92.5% |
| 防抖已知样本误判数 | 10 |
| 瞬时阈值可用样本误判数 | 6 |

**防抖减少跳变但增加进入/退出延迟，所以误判样本数反而更多。** 两种误判数的分母不同，不能直接作为同口径准确率比较；unknown单独报告，不能当作正确。CSV给出每个时间点真值、输入和输出，可自行重新统计延迟与覆盖率。

公开日志有207帧：201个占用输出、0个clear、6个unknown。公开场景没有ROI占用真值，这些仅为算法输出分布，不是检测性能。已有静态背景点足以触发ROI计数，因此真实部署需要背景抑制和带标注验证。

## 验证和限制

6项测试覆盖不均匀采样、持续时间而非帧数、缺测、时钟倒退、中间计数打断确认、无效配置和日志边界。结果包含 `synthetic-decisions.csv` 和 `logged-decisions.csv`，unknown显式写出。

这里没有安全控制、实时告警或人物识别功能。缺测重置发生在收到下一条update时；若应用需要失联告警，必须以本地时间主动调用相应超时处理。默认持续时间与ROI是实验参数，需要按任务的响应延迟和误报代价验证。
