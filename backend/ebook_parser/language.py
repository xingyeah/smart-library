import os
import re
from typing import List, Optional

STRICT_TRAD_CHARS = set(
    "體國學經濟歷專題實踐發電書門開關長見貝車馬鳥語論識產業務動無與為從對會個們這說時過後進種還點樣將應當頭"
    "醫療養護腦神經覺知認知態勢權力變革轉型領導創投職場溝通銷售營銷設計藝術傳記回憶錄哲學宗教歷史政治軍事"
    "數理統計機率線性代數微積分幾何圖論演算法計算機網路資料庫系統架構開發實戰指南導論講義課堂筆記"
    "憂鬱焦慮療癒親密關係情緒習慣覺察冥想靜心斷捨離極簡財富投資買賣股市債券貨幣銀行週期趨勢危機"
    "帝國興衰戰爭革命獨裁民主憲政法理社會階級貧富壟斷資本主義烏托邦預言未來人類文明演化滅絕基因物種"
    "飲食營養睡眠運動減脂斷食長壽癌症免疫病毒細菌醫學臨床診斷處方藥理心理學家經濟學家哲學家歷史學家"
    "台灣香港澳門歐美日本韓國蘇聯俄羅斯德國法國英國羅馬希臘埃及波斯印度伊斯蘭基督教佛教道教儒家"
    "閱讀寫作演講表達邏輯思辨決策博弈談判說服影響力領導力執行力創造力想像力記憶力專注力意志力"
    "小說詩歌散文戲劇電影繪畫建築音樂攝影美學評論鑑賞經典名著暢銷書排行榜獲獎作品集全集選集譯本"
    "萬億兆元角分年月日週時分秒點線面體空間時間速度質量能量力學電磁光學熱學量子相對論宇宙星系地球"
) - set(
    "体国学经济历专题实践发电书门开关长见贝车马鸟语论识产业务动无与为从对会个们这说时过后进种还点样将应当头"
    "医疗养护脑神经觉知认知态势权力变革转型领导创投职场沟通销售营销设计艺术传记回忆录哲学宗教历史政治军事"
    "数理统计机率线性代数微积分几何图论演算法计算机网路资料库系统架构开发实战指南导论讲义课堂笔记"
    "忧郁焦虑疗愈亲密关系情绪习惯觉察冥想静心断舍离极简财富投资买卖股市债券货币银行周期趋势危机"
    "帝国兴衰战争革命独裁民主宪政法理社会阶级贫富垄断资本主义乌托邦预言未来人类文明演化灭绝基因物种"
    "饮食营养睡眠运动减脂断食长寿癌症免疫病毒细菌医学临床诊断处方药理心理学家经济学家哲学家历史学家"
    "台湾香港澳门欧美日本韩国苏联俄罗斯德国法国英国罗马希腊埃及波斯印度伊斯兰基督教佛教道教儒家"
    "阅读写作演讲表达逻辑思辨决策博弈谈判说服影响力领导力执行力创造力想像力记忆力专注力意志力"
    "小说诗歌散文戏剧电影绘画建筑音乐摄影美学评论鉴赏经典名著畅销书排行榜获奖作品集全集选集译本"
    "万亿兆元角分年月日周时分秒点线面体空间时间速度质量能量力学电磁光学热学量子相对论宇宙星系地球"
)


def detect_book_language(
    bnames: Optional[List[str]] = None,
    title: str = "",
    subtitle: str = "",
    author: str = "",
    translator: str = "",
    toc_text: str = "",
    primary_path: str = "",
    is_bilingual: bool = False,
) -> str:
    """
    Classify book content language into:
      - 'zh-CN': 简体中文
      - 'zh-TW': 繁体中文 (台版/港版)
      - 'en':    英文原版
    """
    bnames = bnames or []
    toc_str = toc_text or ""
    title_str = title or ""
    auth_str = author or ""
    trans_str = translator or ""
    path_str = primary_path or (bnames[0] if bnames else "")

    if trans_str and len(re.findall(r"[\u4e00-\u9fff]", trans_str)) >= 2:
        base_lang = "zh"
    else:
        has_cn_chap = bool(
            re.search(
                r"第[0-9一二三四五六七八九十百]+[章回节篇卷部]|前言|序言|导读|目录|结语|后记|附录|参考文献",
                toc_str,
            )
        )
        cjk_toc = len(re.findall(r"[\u4e00-\u9fff]", toc_str))
        en_toc_raw = re.findall(r"[A-Za-z]{3,}", toc_str)
        en_toc_words = [w for w in en_toc_raw if w.lower() not in ("page", "image", "cover", "html", "xhtml", "item")]
        en_toc = len(en_toc_words)

        if has_cn_chap:
            base_lang = "zh"
        elif cjk_toc >= 8 and cjk_toc >= en_toc:
            base_lang = "zh"
        elif en_toc >= 6 and cjk_toc < 5:
            base_lang = "en"
        elif (
            auth_str
            and auth_str != "未知作者"
            and not re.search(r"[A-Za-z]{2,}", auth_str)
            and len(re.findall(r"[\u4e00-\u9fff]", auth_str)) >= 2
        ):
            base_lang = "zh"
        else:
            main_title = re.split(r"[\(（]", title_str)[0].strip()
            cjk_main_title = len(re.findall(r"[\u4e00-\u9fff]", main_title))
            en_main_title = len(re.findall(r"[A-Za-z]{3,}", main_title))

            stem = os.path.splitext(os.path.basename(path_str))[0]
            stem_cjk = len(re.findall(r"[\u4e00-\u9fff]", stem))
            stem_en = len(re.findall(r"[A-Za-z]{3,}", stem))

            if cjk_main_title >= 2 and en_main_title == 0 and en_toc == 0:
                base_lang = "zh"
            elif (
                stem_cjk == 0
                and stem_en >= 2
                and cjk_main_title == 0
                and len(re.findall(r"[\u4e00-\u9fff]", auth_str)) == 0
            ):
                base_lang = "en"
            else:
                m_stem_paren = re.search(
                    r"[\(（]([^\(\)（）]*[\u4e00-\u9fff][^\(\)（）]*)[\)）]", stem
                )
                if m_stem_paren:
                    prefix = stem[: m_stem_paren.start()].strip()
                    prefix_cjk = len(re.findall(r"[\u4e00-\u9fff]", prefix))
                    prefix_words = re.findall(r"[A-Za-z]{2,}", prefix)
                    if (
                        prefix_cjk == 0
                        and len(prefix_words) >= 2
                        and not has_cn_chap
                        and cjk_toc < 5
                    ):
                        base_lang = "en"
                    elif len(prefix_words) < 2 or has_cn_chap or cjk_toc >= 5:
                        base_lang = "zh"
                    else:
                        base_lang = "zh"
                elif en_toc >= 5 and en_toc > cjk_toc * 2:
                    base_lang = "en"
                else:
                    base_lang = "zh"

    if base_lang == "en":
        return "en"

    fname = os.path.basename(path_str)
    check_str = f"{title_str} {subtitle} {auth_str} {trans_str} {fname} {toc_str[:300]}"
    trad_count = sum(1 for ch in check_str if ch in STRICT_TRAD_CHARS)
    return "zh-TW" if trad_count >= 1 else "zh-CN"
