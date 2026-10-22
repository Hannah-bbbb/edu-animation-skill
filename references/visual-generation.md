# 视觉模型 → 可追溯动作原片

先查已认可库存，重点区分闭嘴待机、闭嘴倾听、说话循环、闭嘴反应。根据故事补缺口，不机械生成每个角色的一整套动作。动作生成与背景图生成是不同任务：静态背景可用可用的图像生成工具，角色动作使用图生视频模型；不要将静态示意当成已实现动画。

## 参考和提示词

在目标工程保存唯一角色标准图的原件、SHA256、使用授权/来源和设计说明。派生图另存，不能覆盖原件。参考透明底和边缘杂色不属于角色设计。

按[角色动作提示词模板](../templates/character-action.txt)填写四部分：角色一致性、动作要求、画面与背景、禁止事项。只按角色和动作改变相关内容：

- 说话循环：站位稳定，小幅自然开合嘴；首尾闭嘴姿态匹配，没有结尾致意；不要求对齐某句台词。
- 闭嘴待机/倾听：全程闭嘴，轻呼吸/眨眼或小幅关注；“无音轨”不等于闭嘴。
- 指向：开始姿态、抬手方向、结束指向姿势明确；需 hold 时末帧不能收手。
- 挥手：4秒内抬手、挥手、放下；回待机前确认实际首尾相容。
- 行走/跳跃：明确必要位移，镜头和尺寸稳定，全程留足边距；不能同时要求角色完全不移动。

统一约束：单角色、全身、固定镜头、无缩放/转场，均匀纯绿 `#00FF00`，无地板/阴影/渐变/背景纹理/文字。纸张和彩铅质感仅在角色内。保留可爱比例和稳定服装，禁止五官漂移、额外肢体和形变。角色本身有接近绿幕的配色时优先保护设计并测试连通背景法，不擅自改衣服颜色。

## 付费前检查

本 skill 的 Ark 工具仅实现**一张本地角色参考图 + 文本**，有意不携带多模态广告等无关功能。

1. 用户当轮已授权生成这个缺失动作；仅建议、候选清单、维护 skill 或已有 key 均不等于授权调用。
2. 配方 `job_id` 唯一，角色、动作、用途、版本和 playback 明确。文件名使用 `角色__动作__用途__job_id.mp4`。
3. 请求显式 `resolution=480p`、`duration=4`、默认 `ratio=1:1`、`generate_audio=false`、`watermark=false`。其他比例必须有 `ratio_reason`。不使用 frames 参数。
4. 参考 SHA 与文件一致，提示词已经填完、没有矛盾，4秒内可完成。plan 是格式检查，不能证明语义或生成品质。

## 独立工具调用

Python 3.10+，请求管理只用标准库；不读取任何安装目录或原项目配置。在**目标工程**复制[配方模板](../templates/animation-job.json)，填入角色、具体用途、参考相对路径、真实 SHA 和完整 prompt。模板未填完会拒绝运行。

```bash
# SKILL_DIR 指向本 skill；PROJECT_DIR 指向制作工程，二者不能混用。
python3 "$SKILL_DIR/scripts/ark_video.py" plan jobs/bear-talk-v1.json --workspace "$PROJECT_DIR"
# 读完 plan。环境中设置 ARK_API_KEY，不把值写进命令、配方或日志。
# 将实际 plan_sha256 传入，不使用示例字符串。
python3 "$SKILL_DIR/scripts/ark_video.py" generate jobs/bear-talk-v1.json \
  --workspace "$PROJECT_DIR" --plan-sha256 "$PLAN_SHA"
python3 "$SKILL_DIR/scripts/ark_video.py" status jobs/bear-talk-v1.json --workspace "$PROJECT_DIR"
python3 "$SKILL_DIR/scripts/ark_video.py" download jobs/bear-talk-v1.json --workspace "$PROJECT_DIR"
```

`plan` 不读 key、不联网；`generate` 只提交一次，先落状态再发 POST。查询/下载继续同一 taskId，不自动创建新任务、不自动付费重试。任务文件在 `runtime/ark/<job_id>/task.json`，原片和来源在 `sources/animations/<character>/`。生成状态、原片哈希、prompt和参考哈希保留，密钥不保存。下载不用 Ark Authorization，拒绝重定向，避免把凭证发给CDN。

若 POST 超时、中断、响应没有 ID：保留 `submission_unknown` 或 `submitting`，到控制台查同一次任务。找到后人工核对角色、时间和请求，再运行：

```bash
python3 "$SKILL_DIR/scripts/ark_video.py" attach jobs/bear-talk-v1.json \
  --workspace "$PROJECT_DIR" --task-id "$TASK_ID"
```

工具能核对任务 ID，不能从查询响应证明它就是原提示词。不能删状态重试，也不能绑定不相关任务。确认失败后修改原因，只有在仍有生成授权时创建新 job_id。无已确认免费方式时，不做带 key 的连接测试，绝不以创建视频测连通性。

## 原片验收与背景图

下载成功后 ffprobe 记录实际尺寸、帧率、时长。请求480p/4秒与实际返回像素/时长分别保存；不要为凑参数改原片或重新花钱。播放全片查看角色一致、口型用途、边距、背景和首尾；模型不能保证提示词约束。原片未经认可时先作为候选，不自动替换已认可素材。

背景图：先确定字幕安全区、角色脚底和道具区域；生成独立背景，不让模型直接写教学文字，也不把需要数数/运动的对象烘焙进背景。保留生成提示词、来源和哈希。不要重画已确认角色来适配新背景。

## 官方资料与适用范围

- [方舟创建任务](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)、[查询任务](https://docs.volcengine.com/docs/ark/get-video-generation-task-api?lang=zh)：2026-10-22入口检查为动态页面，正文未通过静态抓取读取。本工具请求形状来自原工程已运行链路；不据此声称验证了平台最新价格、权限或所有模型参数。
- 默认模型沿用 `doubao-seedance-2-0-mini-260615`。新账号/模型不兼容时先核对官方控制台和文档，不自动改模型、提高档位或多次收费试错。
