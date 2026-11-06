"""个人场景评测的故事（v7.19.0）—— personal_eval.py 用

每个故事是一个人和自己的 agent 相处一个多月：几百轮工作和闲聊里，零散说过一些关于自己的事 ——
饮食禁忌、过敏、对 agent 的长期要求、家人、住在哪、后来搬家。之后开一个新会话问问题，
看 agent 能不能看到该看到的（常驻块 + 这一轮的召回），以及塞进了多少不相关的。

故事是为评测写的虚构内容。split：dev 在开发时用；test 是留出集，只在最后对比时跑。

字段
  personal      [(第几天, 用户说的, 助手回的)]  —— 助手的回答有的只是应一声，有的会换个说法复述
  facts         事实名 → 正则。只认用户自己的原话或由它得出的信念行（"user lives in 上海"），
                助手的复述故意换了说法，匹配不上
  probes        q 问题；expect 应该能看到的事实；ok 另外也算相关的正则；
                stale 不该出现的过时信念；kind = personal / work / unrelated
  not_believed  不该记到用户头上的信念 (relation, object)：比如女儿喜欢的东西
"""

STORIES = [
    # ───────────────────────────── 中文 · dev ─────────────────────────────
    {
        "id": "zh-hangzhou", "lang": "zh", "split": "dev", "seed": 11,
        "personal": [
            (2, "我住在杭州，在一家做游戏的公司上班。", "了解。"),
            (5, "我是素食者，不吃肉也不吃海鲜。", "好的，记住了。"),
            (9, "以后回复我都用中文，而且不要用 emoji。", "明白。"),
            (13, "我对花生过敏，吃一点就会起疹子。", "记下了，以后推荐吃的会避开那类坚果。"),
            (17, "我女儿下个月过生日，她特别喜欢恐龙。", "可以考虑恐龙主题的礼物。"),
            (21, "我们组的线上数据库是 PostgreSQL 15，放在阿里云上。", "好的。"),
            (26, "最近工作压力有点大，周末想去爬山放松一下。", "爬山是个放松的好办法。"),
            (33, "我上个月已经搬到上海了，现在在上海上班。", "新城市适应得怎么样？"),
        ],
        "facts": {
            "diet": r"素食|吃素",
            "allergy": r"花生过敏|对花生",
            "no_emoji": r"不要用 ?emoji|别用 ?emoji",
            "daughter": r"女儿.*恐龙|恐龙.*女儿",
            "shanghai": r"搬到上海|住在上海|lives in 上海",
            "hiking": r"爬山",
            "db": r"PostgreSQL",
        },
        "probes": [
            {"q": "今晚吃什么好？给我推荐几个菜", "expect": ["diet", "allergy"]},
            {"q": "帮我给同事写封邮件，说明天请假", "expect": ["no_emoji"]},
            {"q": "给孩子准备什么生日礼物好", "expect": ["daughter"]},
            {"q": "这周末去哪儿玩比较好", "expect": ["shanghai", "hiking"]},
            {"q": "我现在住在哪个城市？", "expect": ["shanghai"], "stale": [r"\] user lives in 杭州"]},
            {"q": "线上数据库要升级大版本，要注意什么", "expect": ["db"], "kind": "work"},
            {"q": "光速大概是多少？", "expect": [], "kind": "unrelated"},
            {"q": "怎么给自行车补胎？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("likes", "恐龙")],
    },
    {
        "id": "zh-chengdu", "lang": "zh", "split": "dev", "seed": 12,
        "personal": [
            (1, "叫我老张就行，别叫我张先生。", "好的老张。"),
            (4, "我在华西医院放射科当医生。", "辛苦了。"),
            (8, "我乳糖不耐受，喝牛奶会拉肚子。", "了解，之后推荐饮品会注意。"),
            (12, "我老婆怀孕五个月了，不能吃生冷的东西。", "恭喜！"),
            (16, "以后回答我简短一点，别超过三段。", "好。"),
            (20, "我儿子今年六岁，在学钢琴。", "六岁学琴挺好的。"),
            (24, "我家养了一只橘猫，叫豆豆。", "名字很可爱。"),
            (30, "我特别讨厌香菜，一点都不能有。", "收到。"),
        ],
        "facts": {
            "name": r"老张",
            "lactose": r"乳糖不耐",
            "wife": r"老婆怀孕|怀孕.*生冷",
            "short": r"简短|别超过三段",
            "son": r"儿子.*钢琴|钢琴.*儿子",
            "cat": r"豆豆",
            "cilantro": r"讨厌香菜|香菜",
            "job": r"放射科",
        },
        "probes": [
            {"q": "下午想喝点东西，推荐一下", "expect": ["lactose"]},
            {"q": "周末全家出去吃饭，帮我挑个餐厅", "expect": ["wife", "cilantro"], "ok": [r"儿子"]},
            {"q": "给我详细讲讲什么是量子计算", "expect": ["short", "name"]},
            {"q": "孩子学琴总是坐不住怎么办", "expect": ["son"]},
            {"q": "猫最近不爱吃饭是怎么回事", "expect": ["cat"]},
            {"q": "我值夜班太累了，有什么提神的办法", "expect": ["job"], "ok": [r"乳糖"]},
            {"q": "珠穆朗玛峰有多高？", "expect": [], "kind": "unrelated"},
            {"q": "怎么叠一只纸鹤？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("uses", "钢琴"), ("likes", "钢琴")],
    },
    # ───────────────────────────── 中文 · test ─────────────────────────────
    {
        "id": "zh-beijing", "lang": "zh", "split": "test", "seed": 13,
        "personal": [
            (2, "我在北京一所高中教语文。", "老师好。"),
            (6, "医生说我心脏不太好，咖啡和浓茶都不能喝了。", "身体要紧。"),
            (10, "以后给我推荐书的时候，不要推荐网络小说。", "明白。"),
            (14, "我妈住在青岛，她有糖尿病。", "老人家要多注意饮食。"),
            (18, "回答里别用表格，我在手机上看不清。", "好的，之后用列表。"),
            (23, "我业余时间喜欢拍照，主要拍街景。", "街拍很有意思。"),
            (31, "我下个月起不在学校了，换到出版社做编辑。", "新工作加油。"),
        ],
        "facts": {
            "heart": r"心脏|不能喝",
            "no_webnovel": r"网络小说",
            "mom": r"我妈.*糖尿病|糖尿病",
            "no_table": r"别用表格|不要用表格",
            "photo": r"拍照|街景",
            "publisher": r"出版社",
        },
        "probes": [
            {"q": "推荐几本适合周末看的书", "expect": ["no_webnovel"]},
            {"q": "帮我列一个这周的工作计划", "expect": ["no_table"], "ok": [r"出版社"]},
            {"q": "我妈过生日，送什么好", "expect": ["mom"]},
            {"q": "下午犯困，喝点什么提神", "expect": ["heart"]},
            {"q": "我现在在哪里工作？", "expect": ["publisher"]},
            {"q": "周末去哪儿拍点东西", "expect": ["photo"]},
            {"q": "地球到月球有多远？", "expect": [], "kind": "unrelated"},
            {"q": "怎么养多肉植物？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("lives_in", "青岛")],
    },
    {
        "id": "zh-shenzhen", "lang": "zh", "split": "test", "seed": 14,
        "personal": [
            (1, "我在深圳做跨境电商，自己当老板。", "了解。"),
            (5, "我不吃牛肉，从小就不吃。", "好的。"),
            (9, "我对青霉素过敏，以前打针差点出事。", "这个很重要，记住了。"),
            (13, "跟我说话别太客气，直接说重点。", "好。"),
            (17, "我男朋友是厨师，在一家粤菜馆。", "那你有口福了。"),
            (21, "我一直在练马拉松，每周跑三次。", "很有毅力。"),
            (28, "我们刚从深圳搬到广州了。", "广州也很好。"),
        ],
        "facts": {
            "beef": r"不吃牛肉",
            "penicillin": r"青霉素",
            "blunt": r"别太客气|直接说重点",
            "boyfriend": r"男朋友.*厨师",
            "marathon": r"马拉松",
            "guangzhou": r"搬到广州|住在广州|lives in 广州",
        },
        "probes": [
            {"q": "我有点感冒发烧，能吃什么药", "expect": ["penicillin"]},
            {"q": "今晚吃什么", "expect": ["beef"], "ok": [r"男朋友"]},
            {"q": "下个月纪念日，送什么礼物好", "expect": ["boyfriend"]},
            {"q": "这周末去哪跑步", "expect": ["guangzhou", "marathon"]},
            {"q": "帮我分析一下要不要多开一个店铺", "expect": ["blunt"], "ok": [r"跨境电商"]},
            {"q": "长颈鹿怎么睡觉？", "expect": [], "kind": "unrelated"},
            {"q": "为什么天空是蓝色的？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("job_title", "厨师")],
    },
    # ───────────────────────────── English · dev ─────────────────────────────
    {
        "id": "en-seattle", "lang": "en", "split": "dev", "seed": 21,
        "personal": [
            (2, "I live in Seattle and work as a backend engineer at a fintech startup.", "Got it."),
            (5, "I'm vegan, so no meat, dairy or eggs for me.", "Noted."),
            (9, "Please never use emoji when you reply to me.", "Understood."),
            (13, "I'm allergic to shellfish, even a little makes me sick.", "Thanks for telling me, I'll keep seafood out of suggestions."),
            (17, "My son Leo turns eight next month and he loves Lego.", "Eight is a fun age."),
            (21, "Our production database is PostgreSQL 15 running on AWS.", "Okay."),
            (26, "Work has been stressful, I want to go kayaking this weekend.", "Kayaking sounds relaxing."),
            (33, "I moved to Portland last month, I work remotely now.", "How are you finding the new city?"),
        ],
        "facts": {
            "vegan": r"vegan",
            "shellfish": r"allergic to shellfish|shellfish",
            "no_emoji": r"never use emoji",
            "son": r"Leo.*Lego|Lego.*Leo|son.*Lego",
            "portland": r"moved to Portland|live in Portland|lives in Portland",
            "kayak": r"kayak",
            "db": r"PostgreSQL",
        },
        "probes": [
            {"q": "What should I cook for dinner tonight?", "expect": ["vegan", "shellfish"]},
            {"q": "Draft an email to my manager asking for Friday off", "expect": ["no_emoji"]},
            {"q": "Any gift ideas for my kid's birthday?", "expect": ["son"]},
            {"q": "Where should I go this weekend?", "expect": ["portland", "kayak"]},
            {"q": "Which city do I live in now?", "expect": ["portland"], "stale": [r"\] user lives in Seattle"]},
            {"q": "We need to upgrade the production database, what should we watch out for?", "expect": ["db"], "kind": "work"},
            {"q": "How fast does light travel?", "expect": [], "kind": "unrelated"},
            {"q": "How do I patch a bike tire?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("likes", "Lego"), ("likes", "lego")],
    },
    {
        "id": "en-london", "lang": "en", "split": "dev", "seed": 22,
        "personal": [
            (1, "Call me Sam, nobody calls me Samantha.", "Sure, Sam."),
            (4, "I'm a nurse at a hospital in London, mostly night shifts.", "That's demanding work."),
            (8, "I'm lactose intolerant, milk makes me ill.", "Good to know."),
            (12, "My wife is five months pregnant, so she avoids raw fish and soft cheese.", "Congratulations!"),
            (16, "Keep your answers short, three paragraphs at most.", "Will do."),
            (20, "We have a beagle called Biscuit.", "Lovely name."),
            (24, "I play chess online most evenings.", "Nice hobby."),
            (30, "I really hate coriander, I can't stand it in anything.", "Noted."),
        ],
        "facts": {
            "name": r"\bSam\b",
            "lactose": r"lactose",
            "wife": r"wife.*pregnant|pregnant",
            "short": r"answers short|three paragraphs",
            "dog": r"Biscuit",
            "chess": r"chess",
            "coriander": r"coriander",
            "job": r"nurse",
        },
        "probes": [
            {"q": "Recommend something to drink this afternoon", "expect": ["lactose"]},
            {"q": "Pick a restaurant for a family dinner this weekend", "expect": ["wife", "coriander"]},
            {"q": "Explain quantum computing in detail", "expect": ["short", "name"]},
            {"q": "My dog has stopped eating, what could be wrong?", "expect": ["dog"]},
            {"q": "How can I stay awake after a long shift?", "expect": ["job"], "ok": [r"lactose"]},
            {"q": "How can I get better at my favourite game?", "expect": ["chess"]},
            {"q": "How tall is Mount Everest?", "expect": [], "kind": "unrelated"},
            {"q": "How do I fold a paper crane?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
    },
    # ───────────────────────────── English · test ─────────────────────────────
    {
        "id": "en-toronto", "lang": "en", "split": "test", "seed": 23,
        "personal": [
            (2, "I teach high school maths in Toronto.", "Nice."),
            (6, "I have coeliac disease, so everything has to be gluten-free.", "Understood."),
            (10, "When you recommend books, no self-help books please.", "Okay."),
            (14, "My mother lives in Vancouver and has type 2 diabetes.", "I hope she's doing well."),
            (18, "Don't put tables in your answers, I read them on my phone.", "I'll use lists instead."),
            (23, "In my spare time I do street photography.", "Sounds fun."),
            (31, "I'm leaving teaching next month to become an editor at a publishing house.", "Good luck with the new job."),
        ],
        "facts": {
            "gluten": r"coeliac|celiac|gluten",
            "no_selfhelp": r"self-help",
            "mom": r"mother.*diabetes|diabetes",
            "no_table": r"tables",
            "photo": r"photography",
            "publisher": r"publishing house|editor",
        },
        "probes": [
            {"q": "Recommend a few books for the weekend", "expect": ["no_selfhelp"]},
            {"q": "Make me a work plan for this week", "expect": ["no_table"], "ok": [r"editor|publishing"]},
            {"q": "What should I get my mum for her birthday?", "expect": ["mom"]},
            {"q": "Suggest a quick lunch I can make", "expect": ["gluten"]},
            {"q": "Where do I work now?", "expect": ["publisher"]},
            {"q": "Where should I go to take some pictures this weekend?", "expect": ["photo"]},
            {"q": "How far is the Moon from Earth?", "expect": [], "kind": "unrelated"},
            {"q": "How do I look after succulents?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("lives_in", "Vancouver")],
    },
    {
        "id": "en-austin", "lang": "en", "split": "test", "seed": 24,
        "personal": [
            (1, "I run a small e-commerce startup in Austin.", "Got it."),
            (5, "I don't eat pork.", "Okay."),
            (9, "I'm allergic to penicillin, I had a bad reaction once.", "That's important, noted."),
            (13, "Be blunt with me and skip the pleasantries.", "Sure."),
            (17, "My boyfriend is a chef at a Thai restaurant.", "Lucky you."),
            (21, "I'm training for a marathon, running three times a week.", "Impressive."),
            (28, "We just moved from Austin to Denver.", "Denver is great."),
        ],
        "facts": {
            "pork": r"don't eat pork",
            "penicillin": r"penicillin",
            "blunt": r"blunt|pleasantries",
            "boyfriend": r"boyfriend.*chef",
            "marathon": r"marathon",
            "denver": r"to Denver|live in Denver|lives in Denver",
        },
        "probes": [
            {"q": "I have a fever and a sore throat, what medicine can I take?", "expect": ["penicillin"]},
            {"q": "What should I have for dinner?", "expect": ["pork"], "ok": [r"boyfriend"]},
            {"q": "What should I get for our anniversary next month?", "expect": ["boyfriend"]},
            {"q": "Where should I go running this weekend?", "expect": ["denver", "marathon"]},
            {"q": "Should I open a second online store?", "expect": ["blunt"], "ok": [r"e-commerce"]},
            {"q": "How do giraffes sleep?", "expect": [], "kind": "unrelated"},
            {"q": "Why is the sky blue?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [("job_title", "chef")],
    },
]


# ───────────────────────── 填充对话：工作和闲聊 ─────────────────────────
# 和个人信息无关的日常使用。用词刻意包含「帮我 / 看看 / 什么 / 怎么」「can you / what is / how do」这类
# 真实对话里常见的说法 —— 词法检索最容易在它们身上误命中。

FILLER = {
    "zh": {
        "topics": ["接口文档", "单元测试", "部署脚本", "性能分析", "代码评审", "前端页面", "日志告警", "数据迁移",
                   "周报", "需求文档", "配置文件", "缓存策略", "定时任务", "权限模块", "消息队列", "埋点数据"],
        "user": ["帮我看看{t}第 {n} 行为什么报错", "帮我改一下{t}，把第 {n} 处的逻辑理顺", "看看{t}还有什么可以优化的地方",
                 "{t}里第 {n} 个用例总是失败，怎么回事", "什么情况下{t}需要重写？", "怎么让{t}跑得更快一点",
                 "帮我整理一下{t}的要点", "解释一下{t}里第 {n} 段在干什么", "今天开了一天会，{t}还没动",
                 "{t}终于搞定了", "明天要给领导讲{t}，帮我理个思路", "为什么{t}在测试环境没问题，线上就不行"],
        "assistant": ["我看了一下，问题可能出在第 {n} 行附近的边界条件，建议先加一条日志确认输入。",
                      "可以先把{t}拆成几个小函数，每个只做一件事，再逐个补测试。",
                      "这类问题通常和环境配置有关，先对比一下两边的版本和参数。",
                      "要点我整理成三条：先确认需求，再看依赖，最后补文档。",
                      "辛苦了，先休息一下，明天再看{t}效率会更高。",
                      "可以，从背景、问题、方案、风险四个方面讲{t}会比较清楚。"],
    },
    "en": {
        "topics": ["the API docs", "the unit tests", "the deploy script", "the profiler output", "the code review",
                   "the landing page", "the alerting rules", "the data migration", "the weekly report",
                   "the spec", "the config file", "the caching layer", "the cron jobs", "the auth module",
                   "the message queue", "the analytics events"],
        "user": ["Can you help me figure out why line {n} in {t} throws an error?",
                 "Can you clean up {t}, especially part {n}?", "What else can we improve in {t}?",
                 "Test case {n} in {t} keeps failing, what's going on?", "When does {t} need a rewrite?",
                 "How do I make {t} run faster?", "Can you summarise the key points of {t}?",
                 "What is section {n} of {t} actually doing?", "Spent the whole day in meetings, haven't touched {t}.",
                 "Finally finished {t}.", "I need to present {t} to my boss tomorrow, help me structure it.",
                 "Why does {t} work in staging but not in production?"],
        "assistant": ["I looked at it; the issue is probably an edge case near line {n}. Add a log line to confirm the input first.",
                      "Try splitting {t} into smaller functions that each do one thing, then add tests one by one.",
                      "This is usually an environment difference; compare versions and settings on both sides.",
                      "Three key points: confirm the requirements, check the dependencies, then update the docs.",
                      "Take a break, you'll be faster on {t} tomorrow.",
                      "Structure it as background, problem, proposal and risks; that works well for {t}."],
    },
}

DAYS = 40              # 故事跨度：个人信息在第 1–33 天说过，问题在第 DAYS+1 天问
FILLER_TURNS = 300     # 每个故事的工作 / 闲聊轮数，均匀撒在这些天里
