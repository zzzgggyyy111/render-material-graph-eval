美术改了材质节点图，编辑器预览和引擎里跑出来的颜色根本不是一回事：拓扑排序按节点声明顺序返回，依赖关系被忽略；检测到环时只报第一个节点，不指出完整环路径；混合节点写成 a+b 而不是 lerp(a,b,factor)，造成色偏；数学节点对负数开方未处理，NaN 继续传播；求值器每次都重新计算中间节点，缓存没有复用；sRGB 与线性色域转换用 gamma 2.2 近似，往返误差超过 1e-9；sRGB→线性与线性→sRGB 使用同一公式，两个方向不互逆；dead_code 只看直接输出，间接未使用的节点漏报；memoize_key 不含参数，改参数仍命中旧值；graph_batch 跨图复用缓存，第二张图命中第一张图的结果。matgraph/evaluate.py 是核心，tests/test_evaluate.py 15个红。要修的是：恢复依赖优先的稳定拓扑排序并在异常里给出完整环路径，按 lerp 与安全数学语义修正节点求值，让 evaluate 对每个可达节点只求值一次，使用精确 sRGB/线性分段公式并保证往返误差小于 1e-9，递归计算 dead_code，让 memoize_key 绑定参数和输入值，并让 graph_batch 为每张图使用独立缓存。

## 运行方式

在仓库根目录执行：

```powershell
cd D:\gsb-20261119\render-material-graph-eval
python -m pytest tests -q
```

初始状态必须恰好是 `15 failed`，且不能出现 collection error、import error 或 syntax error。15 个测试全部因为桩函数抛出 `NotImplementedError` 而失败；完成 `matgraph/evaluate.py` 后应全部通过。

## 字段表

| 字段 | 取值 |
| --- | --- |
| 所属领域 | 游戏、图形与多媒体 |
| 子方向 | 图形渲染 |
| 任务类型 | Bugfix |
| 语言/框架 | Python 3, pytest |
| 环境可复现等级 | 无外部依赖 |

## 数据契约

- `graph["nodes"]` 是 `id -> node` 映射，映射顺序即声明顺序；`graph["edges"]` 的元素是 `(src_id, dst_id, port)`，表示 `src_id` 必须先于 `dst_id` 求值，`port` 是目标节点的输入端口名。
- 节点是含 `"type"` 与可选 `"params"` 的映射；支持的节点类型为 `constant`、`texture`、`mix`、`math`。
- 数值可以是标量，也可以是三分量颜色元组。颜色空间仅包括 `"srgb"` 与 `"linear"`；转换必须使用精确分段公式，不得用 gamma 2.2 近似。
- 所有数值断言使用绝对容差 `1e-9`；转换往返误差必须严格小于 `1e-9`。

## 评测输入

`tests/test_evaluate.py` 不可修改，只能修改 `matgraph/evaluate.py`。可以增加私有辅助函数，但必须保留题面要求的公开函数签名与契约。
