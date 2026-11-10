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
  block_changes 说法改了的（v7.22.0）：常驻块「当前」那几段里 now 要在、was 不能在
  followups     待跟进（v7.23.0）：常驻块「Follow-ups」那一段里 show 要在、hide 不能在、allow 在不在都行。
                新会话是第 41 天 = 2026-10-12（周一）上午；个人信息在第 d 天晚上说（第 d 天 = 2026-09-01 + d 天）。
                hide 的几种：早就过去了、说过结果了、取消了、还早（后天以后）、只是假设（如果…）
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
        "block_changes": [{"now": r"搬到上海", "was": r"住在杭州"}],
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
        "block_changes": [{"now": r"出版社", "was": r"高中教语文"}],
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
        "block_changes": [{"now": r"moved to Portland", "was": r"live in Seattle"}],
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

    # ───────────────── 改主意（v7.22.0）：说过的要求、饮食、住处、称呼、喜好后来变了 ─────────────────
    # 每个故事的 block_changes：常驻块「当前」那几段里，now 要在、was 不能在（可以出现在「已经改了」那段里）
    {
        "id": "zh-wuhan", "lang": "zh", "split": "dev", "seed": 31,
        "personal": [
            (2, "以后回复我都用中文，不要用 emoji。", "好的。"),
            (4, "我是素食者，不吃肉。", "记住了。"),
            (6, "我住在武汉，在一家银行上班。", "了解。"),
            (8, "叫我小周就行。", "好的小周。"),
            (10, "我特别喜欢喝冰美式。", "冰美式提神。"),
            (12, "我对芒果过敏。", "记下了，推荐水果会避开芒果。"),
            (25, "以后回复我用英文吧，我在练英语。", "Sure, I'll reply in English from now on."),
            (29, "我上个月搬到深圳了，换了家互联网公司。", "新环境加油。"),
            (32, "我现在不吃素了，开始吃肉了。", "好的，了解。"),
            (35, "以后叫我阿周吧。", "好的阿周。"),
            (38, "最近不怎么喝冰美式了，改喝热拿铁。", "热拿铁也不错。"),
        ],
        "facts": {
            "english": r"用英文",
            "no_emoji": r"不要用 ?emoji",
            "meat": r"开始吃肉|不吃素了",
            "mango": r"芒果过敏|对芒果",
            "shenzhen": r"搬到深圳|lives in 深圳",
            "azhou": r"阿周",
            "latte": r"热拿铁",
        },
        "probes": [
            {"q": "用中文还是英文回复我？", "expect": ["english"]},
            {"q": "帮我写一段生日祝福发给同事", "expect": ["english", "no_emoji"]},
            {"q": "今晚吃什么？推荐几个菜", "expect": ["meat", "mango"]},
            {"q": "我现在住在哪个城市？", "expect": ["shenzhen"], "stale": [r"\] user lives in 武汉"]},
            {"q": "你一般怎么称呼我？", "expect": ["azhou"]},
            {"q": "下午帮我点杯咖啡", "expect": ["latte"]},
            {"q": "长城有多长？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "block_changes": [
            {"now": r"用英文", "was": r"都用中文"},
            {"now": r"搬到深圳", "was": r"住在武汉"},
            {"now": r"不吃素了|开始吃肉", "was": r"我是素食者"},
            {"now": r"阿周", "was": r"小周"},
            {"now": r"热拿铁", "was": r"喜欢喝冰美式"},
        ],
    },
    {
        "id": "en-boston", "lang": "en", "split": "dev", "seed": 32,
        "personal": [
            (2, "Please always reply in English, and keep your answers short.", "Will do."),
            (4, "I'm vegan.", "Noted."),
            (6, "I live in Boston and work as a nurse.", "Got it."),
            (8, "Call me Sam.", "Sure, Sam."),
            (10, "I love horror movies.", "Any favourites?"),
            (12, "I'm allergic to peanuts.", "I'll keep that in mind."),
            (25, "Actually, from now on answer in Spanish, I'm practicing for a trip.", "Claro."),
            (29, "We moved to Denver last month, and I'm a nurse at a children's hospital now.", "Congrats on the move."),
            (32, "I'm not vegan anymore, I eat fish and eggs now.", "Okay, noted."),
            (35, "Please call me Samantha from now on.", "Of course, Samantha."),
            (38, "I don't really enjoy horror movies anymore, I prefer comedies.", "Comedies it is."),
        ],
        "facts": {
            "spanish": r"in Spanish",
            "short": r"answers short",
            "fish": r"not vegan anymore|eat fish",
            "peanut": r"allergic to peanuts",
            "denver": r"moved to Denver|lives in Denver",
            "samantha": r"Samantha",
            "comedy": r"prefer comedies",
        },
        "probes": [
            {"q": "Which language should you answer me in?", "expect": ["spanish"]},
            {"q": "Write a quick note to my landlord about the broken heater", "expect": ["spanish", "short"]},
            {"q": "What should I cook for dinner tonight?", "expect": ["fish", "peanut"]},
            {"q": "Which city do I live in now?", "expect": ["denver"], "stale": [r"\] user lives in Boston"]},
            {"q": "What name should you use for me?", "expect": ["samantha"]},
            {"q": "Recommend a movie for tonight", "expect": ["comedy"]},
            {"q": "How tall is Mount Everest?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "block_changes": [
            {"now": r"in Spanish", "was": r"reply in English"},
            {"now": r"moved to Denver", "was": r"live in Boston"},
            {"now": r"not vegan anymore", "was": r"\] I'm vegan"},
            {"now": r"Samantha", "was": r"Call me Sam\b"},
            {"now": r"prefer comedies", "was": r"I love horror movies"},
        ],
    },
    {
        "id": "zh-xiamen", "lang": "zh", "split": "test", "seed": 33,
        "personal": [
            (1, "跟我说话请用简体中文。", "好的。"),
            (3, "我一直吃素，蛋奶也不碰。", "了解。"),
            (6, "我住在厦门，在一家外贸公司做会计。", "了解。"),
            (9, "回答别带表情符号。", "好。"),
            (12, "我最爱吃榴莲。", "口味很独特。"),
            (14, "我对青霉素过敏。", "记住了。"),
            (24, "以后就用繁体字跟我说吧，我在给台湾的客户写邮件。", "好的。"),
            (28, "我已经搬去福州了，还是做会计。", "福州也不错。"),
            (31, "我最近开始吃肉了，医生让我补充蛋白质。", "注意均衡。"),
            (36, "我现在不太爱吃榴莲了，太腻。", "口味会变。"),
        ],
        "facts": {
            "traditional": r"繁体",
            "no_emoji": r"别带表情|表情符号",
            "meat": r"开始吃肉",
            "penicillin": r"青霉素",
            "fuzhou": r"搬去福州|lives in 福州",
            "durian_now": r"不太爱吃榴莲",
        },
        "probes": [
            {"q": "回复我的时候用简体还是繁体？", "expect": ["traditional"]},
            {"q": "帮我给客户写封节日问候邮件", "expect": ["traditional", "no_emoji"]},
            {"q": "晚饭吃什么好？", "expect": ["meat"]},
            {"q": "感冒了能吃什么药？", "expect": ["penicillin"]},
            {"q": "我现在住哪个城市？", "expect": ["fuzhou"], "stale": [r"\] user lives in 厦门"]},
            {"q": "推荐点水果", "expect": ["durian_now"]},
            {"q": "月球离地球多远？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "block_changes": [
            {"now": r"繁体", "was": r"请用简体中文"},
            {"now": r"搬去福州", "was": r"住在厦门"},
            {"now": r"开始吃肉", "was": r"我一直吃素"},
            {"now": r"不太爱吃榴莲", "was": r"最爱吃榴莲"},
        ],
    },
    {
        "id": "en-chicago", "lang": "en", "split": "test", "seed": 34,
        "personal": [
            (1, "Answer me in English, please.", "Sure."),
            (3, "I'm vegetarian.", "Noted."),
            (6, "I live in Chicago and teach high school math.", "Got it."),
            (9, "Please don't use bullet points in your replies.", "Okay."),
            (12, "I hate jazz.", "Fair enough."),
            (14, "I'm allergic to latex.", "Noted."),
            (24, "From now on, please reply in French. I'm moving to Paris next year.", "D'accord."),
            (28, "I moved to Milwaukee in June; still teaching math.", "Hope the move went well."),
            (31, "I've started eating meat again.", "Okay."),
            (36, "Honestly I've come around on jazz, I listen to it every evening now.", "Nice."),
        ],
        "facts": {
            "french": r"in French",
            "no_bullets": r"bullet points",
            "meat": r"eating meat again",
            "latex": r"allergic to latex",
            "milwaukee": r"moved to Milwaukee|lives in Milwaukee",
            "jazz_now": r"come around on jazz|listen to it every evening",
        },
        "probes": [
            {"q": "Which language do you reply to me in?", "expect": ["french"]},
            {"q": "Draft a short message to my school's principal", "expect": ["french", "no_bullets"]},
            {"q": "What should I make for dinner?", "expect": ["meat"]},
            {"q": "I need to buy gloves for cleaning, any tips?", "expect": ["latex"]},
            {"q": "Which city do I live in now?", "expect": ["milwaukee"], "stale": [r"\] user lives in Chicago"]},
            {"q": "Suggest some music for tonight", "expect": ["jazz_now"]},
            {"q": "What is the boiling point of water?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "block_changes": [
            {"now": r"in French", "was": r"Answer me in English"},
            {"now": r"moved to Milwaukee", "was": r"live in Chicago"},
            {"now": r"eating meat again", "was": r"\] I'm vegetarian"},
            {"now": r"come around on jazz", "was": r"I hate jazz"},
        ],
    },

    # ───────────────── 待跟进（v7.23.0）：有日子、有结果的事 ─────────────────
    # 新会话 = 第 41 天（2026-10-12 周一）。第 30 天 = 10-01 周四，第 34 天 = 10-05 周一，第 40 天 = 10-11 周日
    {
        "id": "zh-fu-shenzhen", "lang": "zh", "split": "dev", "seed": 41,
        "personal": [
            (3, "我住在深圳，在一家做硬件的公司当产品经理。", "了解。"),
            (8, "我对青霉素过敏。", "记住了。"),
            (12, "明天要考驾照科目一，有点慌。", "加油，按题库多刷几遍。"),               # 09-14：早就过去了
            (30, "下周四要去上海出差三天。", "注意休息。"),                             # 10-08，后来取消了
            (31, "下周三我有个腾讯的二面，有点紧张。", "提前准备一下项目经历，祝顺利。"),   # 10-07：还不知道结果
            (33, "下个月20号去日本旅行，已经订好机票了。", "日本这个季节很舒服。"),        # 11-20：还早
            (34, "我女儿这周五学校开运动会，她报了跑步。", "让她加油！"),                 # 10-09：还不知道结果
            (35, "上海出差取消了，客户那边改成线上会。", "那也省得跑一趟。"),
            (36, "下周二要去医院体检，前一天晚上八点以后不能吃东西。", "记得提前空腹。"),  # 10-13：明天
            (37, "这周六要带我妈去医院复查。", "希望一切顺利。"),                        # 10-10，已经说了结果
            (38, "如果下周有面试的话，穿什么比较好？", "商务休闲就可以。"),               # 假设
            (40, "我妈复查结果出来了，医生说恢复得不错。", "太好了！"),
        ],
        "facts": {"allergy": r"青霉素", "city": r"深圳"},
        "probes": [
            {"q": "我感冒了，能吃什么药？", "expect": ["allergy"]},
            {"q": "地球到太阳有多远？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "followups": {"show": [r"二面", r"体检", r"运动会"],
                      "hide": [r"科目一", r"上海出差", r"复查", r"日本", r"如果下周有面试"]},
    },
    {
        "id": "en-fu-denver", "lang": "en", "split": "dev", "seed": 42,
        "personal": [
            (3, "I live in Denver and work as a nurse at a children's hospital.", "Got it."),
            (8, "I'm allergic to shellfish.", "Noted."),
            (12, "I have my driving test tomorrow morning, wish me luck.", "Good luck!"),
            (30, "I'm flying to Seattle next Thursday for a conference.", "Have a great trip."),
            (31, "I have a job interview at a clinic next Wednesday, pretty nervous.", "You'll do great, prepare a few stories."),
            (33, "We're going to Japan on November 20th, flights are booked.", "Japan in late autumn is lovely."),
            (34, "My son has a swim meet this Friday, he's racing the 50 metres.", "Tell him good luck!"),
            (35, "The Seattle trip got cancelled, they moved the conference online.", "That saves you the travel."),
            (36, "I have a dentist appointment next Tuesday.", "Remember to floss."),
            (37, "Taking my dad to his cardiology checkup this Saturday.", "Hope it goes well."),
            (38, "If I ever get an interview next week, what should I wear?", "Smart casual works."),
            (40, "Dad's checkup went fine, the doctor is happy with his heart.", "Great news!"),
        ],
        "facts": {"allergy": r"shellfish", "city": r"Denver"},
        "probes": [
            {"q": "Any ideas for a quick dinner tonight?", "expect": ["allergy"]},
            {"q": "What is the capital of Australia?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "followups": {"show": [r"job interview", r"dentist", r"swim meet"],
                      "hide": [r"driving test", r"Seattle", r"cardiology|checkup", r"Japan", r"If I ever get an interview"]},
    },
    {
        "id": "zh-fu-nanjing", "lang": "zh", "split": "test", "seed": 43,
        "personal": [
            (2, "我叫小林，住在南京。", "你好小林。"),
            (6, "我不吃辣，一点都不行。", "记住了。"),
            (14, "后天要参加一个婚礼，帮我想想随多少份子钱。", "看关系远近，一般同事两三百就可以。"),  # 09-17：早就过去了
            (29, "10月8号要参加公司的晋升答辩。", "提前准备好 PPT。"),                  # 10-08：还不知道结果
            (30, "周日约了朋友去爬紫金山。", "天气不错的话很舒服。"),                     # 10-04：列不列都行
            (32, "我老公下周一做个小手术，切阑尾。", "希望一切顺利。"),                   # 10-05，已经说了结果
            (35, "月底要交论文初稿，压力好大。", "可以先列提纲。"),                       # 10-31：还早
            (36, "老公手术很顺利，已经出院了。", "太好了，让他多休息。"),
            (37, "我妹妹下周六结婚，我是伴娘。", "恭喜！"),                              # 10-17：还早
            (38, "13号去拔智齿，有点怕。", "拔完记得冰敷。"),                             # 10-13：明天
            (39, "后天我要去北京参加一个行业展会。", "路上注意安全。"),                   # 10-12：今天
        ],
        "facts": {"spicy": r"不吃辣", "city": r"南京"},
        "probes": [
            {"q": "推荐几道家常菜", "expect": ["spicy"]},
            {"q": "水的沸点是多少度？", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "followups": {"show": [r"答辩", r"展会", r"拔智齿"],
                      "hide": [r"婚礼|份子钱", r"阑尾|手术", r"论文初稿", r"伴娘|结婚"], "allow": [r"紫金山"]},
    },
    {
        "id": "en-fu-portland", "lang": "en", "split": "test", "seed": 44,
        "personal": [
            (2, "I'm Priya, I live in Portland.", "Nice to meet you, Priya."),
            (6, "I'm lactose intolerant.", "Noted."),
            (15, "My final exam is the day after tomorrow, I'm so stressed.", "You've got this."),
            (29, "I'm defending my thesis on October 8th.", "Big day! Practise the Q&A."),
            (30, "Going hiking with friends this Sunday.", "Enjoy!"),
            (32, "My wife is having knee surgery next Monday.", "Wishing her a smooth recovery."),
            (35, "My paper draft is due at the end of the month.", "Start with an outline."),
            (36, "Her surgery went well, she's already home.", "Great to hear."),
            (37, "My sister is getting married next Saturday, I'm the maid of honor.", "Congratulations!"),
            (38, "Getting my wisdom teeth out on the 13th, not looking forward to it.", "Ice packs help afterwards."),
            (39, "Heading to a trade show in Las Vegas in two days.", "Safe travels!"),
        ],
        "facts": {"lactose": r"lactose", "city": r"Portland"},
        "probes": [
            {"q": "What should I get at the coffee shop?", "expect": ["lactose"]},
            {"q": "How many moons does Mars have?", "expect": [], "kind": "unrelated"},
        ],
        "not_believed": [],
        "followups": {"show": [r"thesis", r"trade show", r"wisdom teeth"],
                      "hide": [r"final exam", r"knee surgery|surgery went well", r"paper draft",
                               r"maid of honor|getting married"], "allow": [r"hiking"]},
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
