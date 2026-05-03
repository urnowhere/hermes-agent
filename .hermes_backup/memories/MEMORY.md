用户使用 interview skill 管理招聘流程，文件路径含空格需用引号。PDF读取用 pypdf（pdftotext不可用）。日期用 `date "+%Y-%m-%d"` 确认，不要硬编码。面试时间表：/mnt/c/Users/liuch/My Documents/warpdriveai/制度/招聘/面试时间表.md；入职时间表：/mnt/c/Users/liuch/My Documents/warpdriveai/制度/招聘/入职时间表.md；候选人档案：/mnt/c/Users/liuch/My Documents/warpdriveai/制度/招聘/候选人档案/
§
User requires all model calls to use max thinking/reasoning effort globally. Set agent.reasoning_effort: xhigh and delegation.reasoning_effort: xhigh in config.yaml. Note: "xhigh" is the highest available effort level — "max" is not a valid config value.
§
Remote 10.10.70.88: jianliu, sudo password="!QAZ2wsx", Ubuntu 22.04, 8× RTX 5090 (CC 12.0), driver 580.126.09, CUDA 13.0, nvcc 13.0.88, NCCL 2.28.9-1+cuda13.0, nccl-tests @ ~/work/bandwidth_test/nccl-tests/build/. DeepSeek-V4-Flash @ ~/work/models/deepseekv4_flash/ (149G). vLLM script @ ~/work/vllm_server/run_vllm_deepseekv4.sh (expert-parallel). Docker 29.4 CDI quirk: --gpus all fails, use --device='nvidia.com/gpu=all'. nvidia-container-toolkit installed.
§
用户要求安装来自 https://clawhub.ai/wallstreetinsights/wallstreetcn-news 的 skill。
§
懂车帝反爬严重，使用 dongchedi_watch 工具时 headless=true 会返回空数据（matched_count=0），必须使用 headless=false 才能正常获取数据。
§
懂车帝爬虫任务执行时间长（约1.5小时），可能因验证码导致首次静默失败后重试成功。
§
用户要求创建一个技能，用于备份所有记忆、技能和cron任务到代码目录下的.hermes_backup文件夹，且需要自动验证并持续运行直到成功。
§
用户要求将备份所有数据库的功能添加到已有的技能中。
§
面试管理技能已创建，包含5个核心功能：简历归档、简历摘要、面试时间表、入职时间表、候选人档案。文件路径约定：简历目录为 /mnt/c/Users/liuch/My Documents/warpdriveai/制度/招聘/简历，下载目录为 /mnt/c/Users/liuch/Downloads，档案目录为 /mnt/c/Users/liuch/My Documents/warpdriveai/制度/招聘/候选人档案，时间表为 .../招聘/面试时间表.md，入职表为 .../招聘/入职时间表.md。
§
Hermes 中技能的 slash 命令由 frontmatter 中的 name 字段自动生成，trigger 字段仅用于文档说明，不实际注册命令。
§
用户要求每天晚上10点执行 /dongchedi-l90-watch 脚本，结果需要包含商家地址信息和车型信息，新增和下架的信息需要额外给出，最后给出车型的统计。
§
在 WSL 环境下抓取懂车网数据时，使用 xvfb-run 解决无 X server 问题，这是一个需要试错和调整的非平凡方法。
§
用户新增了一个面试管理技能，简历主目录为 '/mnt/c/Users/liuch/My Documents/warpdriveai/制度/招聘/简历' 和 '/mnt/c/Users/liuch/Downloads/'，技能功能包括：检查简历是否在 Downloads 目录而非简历目录，根据候选人姓名移动简历，以及总结候选人优劣势。
§
做梦机制已实现：~/.hermes/scripts/dream_cycle.py，四阶段流水线（calibrate→collect→merge→index）。cron job ID 1b38a660ab9a，每天3:00执行，deliver=origin（推微信）。LLM直接HTTP调用DeepSeek V4，max30次/run，有状态追踪和预算管理。