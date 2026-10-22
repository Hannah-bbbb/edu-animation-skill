# 原英语启蒙工程的适配索引

仅在目标工程实际存在以下文件时采用本页命令。这里记录可复用接口，不携带课程、角色素材、声音、模型或完整渲染器；不要把此页当成自动安装指令。提炼基线为原工程提交 `b4b37a5`，后续先核对真实代码和项目指南。

## 入口与实现

| 任务 | 原工程入口 |
| --- | --- |
| 内容和执行约定 | `docs/CURRICULUM.md`、`docs/WORKFLOW.md`、`docs/STATUS.md`；命令查`README.md` |
| 课程/资产/声线 | `content/episodes/`、`config/catalog.json`、`content/voices/` |
| 方舟原片 | `scripts/seedance-animation.py`、`config/seedance.json`、`content/animation-jobs/` |
| 原片转透明帧 | `scripts/prepare-bear-animations.py`；两角色配置在`config/*-animations.json` |
| 新动作合并/统一720画布 | `scripts/seedance_import.py`；prepare与update-layout分开 |
| Godot播放 | `godot/momo-wave/sprite_character.gd`、各角色profile与测试场景 |
| 课程图集 | `scripts/pack-character-frames.py`、`config/frame-animation-packs.json` |
| 帧选择/口型 | `src/video/motion/frame-animation.ts`中的`getFrameSpeechTiming`、`src/video/components/FrameCharacter.tsx` |
| 连续场景 | `src/video/motion/`中的实际事件族及对应`components/*StoryScene.tsx` |
| 本地TTS | `scripts/qwen-voice.py`、`src/audio/`、`requirements-local-voice.lock.txt` |
| 构建/检查/交付 | `scripts/episode.ts`、`src/pipeline/`、`src/qc/`、`src/pipeline/delivery.ts` |
| 媒体音轨参照 | `src/qc/soundtrack.ts`中的`audioDelaySamples`；保持与固定渲染器舍入一致 |
| 视频收藏/追溯 | `scripts/collect-videos.ts`、`scripts/library.ts`、`成片目录.md` |

本skill独立工具的JSON是便携版协议，**不能直接覆盖上述原工程配置**。原工程已有防重提交、导入合并和来源冻结时优先复用原命令；便携工具用于新工程或独立素材批次。移植新增能力须核对实际schema。

## 已有完整课程链路

```bash
npm run doctor
npm run episode -- validate "$EPISODE_FILE"
npm run episode -- build "$EPISODE_FILE" --profile preview
# 从输出读取准确runDir；不要猜路径或并发操作同一课程锁。
npm run episode -- qc "$PREVIEW_RUN_DIR"
npm run episode -- build "$EPISODE_FILE" --profile production
npm run episode -- qc "$HD_RUN_DIR"
npm run episode -- export "$HD_RUN_DIR" --draft
npm run videos:collect
npm run library
```

新主题不能只增加一个JSON：核对schema枚举、validator事件/对象对应、inputs冻结注册、主题装载、视频与封面分派、资源登记和实际绘制路径。先做必要最小实现再build。不要编造schema字段或改快照通过校验。

原工程图集契约为完整720画布转换到512图格、8列，layout沿用Godot原720坐标；独立`pack_atlas.py`另提供canvas_transform，接入时先换算，不能混读两套字段。原源动作16 FPS与输出30 FPS分开记录。

原工程使用正式声线ID选择而不是临时试听路径；当前实际声线以项目状态和catalog为准。本页不复制易过期的集数、待办清单或本机绝对路径。
