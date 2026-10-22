# 原片 → 透明帧 → Godot / 课程图集

## 固定画布抠像

依赖 Python 3.10+、NumPy、OpenCV和FFmpeg/ffprobe。复用工程已锁定环境；新环境在工程内建 venv 并锁版本，不在 skill 中装依赖。脚本不自动安装或下载。

```bash
python "$SKILL_DIR/scripts/prepare_animation.py" sources/animations/bear \
  --output godot/animations/bear-batch-v1 --config animation-config.json \
  --ffmpeg "$FFMPEG"
```

从目标工程运行；输入目录内所有MP4（不递归）按16 FPS采样，每片按原文件主干建子目录，生成 RGBA PNG、每动作 `animation.json` 及总表 `animations.json`。输出目录必须是新目录：整个批次成功后才发布，拒绝覆盖旧素材。支持中文文件名。模板见[抠像配置](../templates/animation-config.json)。

先把实际原片主干映射为播放动作名，并将生成配方的playback抄入本次配置。工具不会猜文件名中的用途或自动读取旁边的生成配方。例如源片叫`bear__wave_v1__挥手__bear-wave-v1.mp4`，对应：

```json
{
  "actions": {"bear__wave_v1__挥手__bear-wave-v1": "wave"},
  "playback": {"wave": "return_idle"}
}
```

这是字段示意，合并到完整配置中；此批要直接测试return_idle时还需包含循环idle，或者在已检查的目标库中合并。`layouts`和`closed_mouth`按映射后的动作名填写，`overrides`按原文件主干填写。未指定playback时保守使用hold，未校准layout时用画布底部中点作临时锚点，不能把它当成已校准脚底。

采样不会创造新姿态。保留源画布、像素坐标与跳跃/下蹲/移动，不逐帧裁剪、居中或缩放。实际4秒稍有超出时可能多一帧，记录真实帧数，不凭时长硬写64帧。

参数含义：

- `green_rgb`：屏幕主色，默认 `[0,255,0]`；实际黄绿底可逐片覆盖。
- `tolerance` / `softness`：OpenCV HSV色相单位，1单位为2度；容差更宽可能误删青色衣服。
- `value_min` / `green_excess_min`：保护暗轮廓及不明显偏绿的色块。
- `despill`：仅在透明边缘压低残绿，不对整件衣服去绿。
- `edge_shrink`：小范围收边，0–3像素；过大会吃掉胡须/手指。
- `connected_background`：仅去与画布外缘连通的背景，保护封闭轮廓内绿色饰物；可能留下完全封闭的外部绿隙，须看图。
- `remove_light_border`：用于已确认的纸色背景缝隙；优先与连通模式组合，不能对所有白色像素全局去除。若白色身体触边/轮廓破口，仍可能误删，源片裁切优先退回修正。
- `keep_largest_component`：仅适用于单个连通角色、无独立道具的源片。可能删掉手持物、尾巴碎段等，不默认启用。

这些是工程化颜色抠像，不是语义分割。先检查首中末和最大幅度，再扫全动作；在浅、深、棋盘背景上看边缘、白毛、青衣、细线和脚底。不要只看透明预览的黑底。绿色贴近角色颜色且无法分离时明确限制，勿无限扩大容差。

## 大小与位置校准

相同分辨率不代表角色一样大。选择一个完整、自然站立的参考帧；记录原始像素脚底锚点 `anchor=[x,y]`，以该动作的自然站姿与参考身高求一个固定 `scale`。不按举手后的总包围盒或跳跃高度推身高；脚被裁掉的源片不能视为完整身高，只能明确限制为半身/遮挡用途。

坐标约定：PNG左上角为原点，播放器 Node2D 原点为舞台脚底。每动作整段保持：

```text
sprite.centered = false
sprite.scale = (scale, scale)
sprite.position = -anchor * scale
```

在 `animations.json` 的动作 `layout` 写校准值，校准全片不会改变原PNG。外部整体舞台缩放再乘一次即可。记录校准依据和源哈希；从抠像工具重新生成批次会回到配置中的layout，因此定稿值须同步保存回工程配置。全动作检查耳朵/手脚边距和动作切换，不逐帧补偿角色的自然运动。

## Godot 4接入

将[轻量播放器](../scripts/sprite_character.gd)复制到目标Godot工程，挂在Node2D上，Inspector的 `manifest_path` 指向生成总表，如 `res://animations/bear-batch-v1/animations.json`。脚本创建一个 AnimatedSprite2D，并提供 `play_action("wave")`，不需要骨骼或额外状态机。

- `loop`：idle、walk_side等循环。是否闭嘴由实际帧核对，名称不保证。
- `return_idle`：wave、jump、nod_fast、cheer等结束后回 `idle_action`。该动作必须存在且是循环。
- `hold`：point_upper_right等停留最后一帧；原片最后一帧必须仍是所需指向。

启用Inspector中的 `keyboard_test` 后，键盘1–9切换总表前九个动作，或从现有按钮调用 `play_action`。重复逐帧调用相同动作不会重启；要重播单次动作传 `restart=true`。测试场景放一个背景和一个角色就够，不开发动画编辑器。

播放器按需从完整PNG加载帧，开发/本地测试直接可用。若导出PCK，必须在Godot导出设置包含PNG原文件及JSON（原始文件按路径动态读入），否则不能假定编辑器可播就能打包；或者在目标工程转换为受追踪的SpriteFrames资源后导出。验证循环不中断、单次回待机、保持末帧、未知动作拒绝、多个实例不共享播放状态。

官方语义：[AnimatedSprite2D](https://docs.godotengine.org/en/stable/classes/class_animatedsprite2d.html)使用SpriteFrames；循环动作不发出 `animation_finished`，非循环结束会暂停。因此单次回待机用该信号，hold维持末帧。

## 图集复用到离线视频

```bash
python "$SKILL_DIR/scripts/pack_atlas.py" godot/animations/bear-batch-v1/animations.json \
  --output public/frame-atlases/bear-v1 --cell-size 512 --columns 8
```

整个画布等比缩放并居中补透明边到固定图格，不按角色重排；输出新的 `atlases.json`，保留源画布尺寸、固定画布变换、播放方式、layout与源帧/图集哈希。缩放时先预乘alpha、重采样后再还原，减少黑边。内存或最大纹理尺寸不足时按动作或页拆分，不能无限堆成一张大图。

`atlas_index = floor(elapsed_seconds * source_fps)`。loop对帧数取模；hold钳制至末帧；return_idle结束后切到idle并使用剩余时间。输出30 FPS时按输出时间取源帧，不加速原16 FPS。

图集中坐标先应用 `canvas_transform`：`p_cell = p_source * scale + offset`，anchor也用相同变换；然后再应用该动作layout.scale与舞台缩放。不要直接把原画布anchor当成512图格坐标。首次接入查看脚底和跨动作大小是否保持。

总表可包含未审核候选，只有实际核对并认可的动作才加入课程；`closed_mouth=true`只能在真实帧检查后填写，不自动推断。新批次先单独输出，合并已有库前检查重名、哈希、FPS、画布和布局，不直接覆盖旧动作。
