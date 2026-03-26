#!/usr/bin/env python3
"""
早朝简报采集脚本
每日 06:00 自动运行，抓取全球新闻 RSS → data/morning_brief_YYYYMMDD.json
覆盖: 政治 | 军事 | 经济 | AI大模型
"""
import json, pathlib, datetime, subprocess, re, sys, os, logging
from xml.etree import ElementTree as ET
from file_lock import atomic_json_write
from utils import validate_url

log = logging.getLogger('朝报')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

DATA = pathlib.Path(__file__).resolve().parent.parent / 'data'

# ── RSS 源配置 ──────────────────────────────────────────────────────────
FEEDS = {
    '政治': [
        ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
        ('Reuters World', 'https://feeds.reuters.com/reuters/worldNews'),
        ('AP Top News', 'https://rsshub.app/apnews/topics/ap-top-news'),
    ],
    '军事': [
        ('Defense News', 'https://www.defensenews.com/rss/'),
        ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
        ('Reuters', 'https://feeds.reuters.com/reuters/worldNews'),
    ],
    '经济': [
        ('Reuters Business', 'https://feeds.reuters.com/reuters/businessNews'),
        ('BBC Business', 'https://feeds.bbci.co.uk/news/business/rss.xml'),
        ('CNBC', 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
    ],
    'AI大模型': [
        ('Hacker News', 'https://hnrss.org/newest?q=AI+LLM+model&points=50'),
        ('VentureBeat AI', 'https://venturebeat.com/category/ai/feed/'),
        ('MIT Tech Review', 'https://www.technologyreview.com/feed/'),
    ],
}

CATEGORY_KEYWORDS = {
    '军事': ['war', 'military', 'troops', 'attack', 'missile', 'army', 'navy', 'weapons',
              '战', '军', '导弹', '士兵', 'ukraine', 'russia', 'china sea', 'nato'],
    'AI大模型': ['ai', 'llm', 'gpt', 'claude', 'gemini', 'openai', 'anthropic', 'deepseek',
                'machine learning', 'neural', 'model', '大模型', '人工智能', 'chatgpt'],
}

def curl_rss(url, timeout=10):
    """用 curl 抓取 RSS"""
    try:
        from urllib.request import Request, urlopen
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; MorningBrief/1.0)'})
        response = urlopen(req, timeout=timeout)
        return response.read().decode('utf-8', errors='ignore')
    except Exception:
        return ''

def _safe_parse_xml(xml_text, max_size=5*1024*1024):
    """安全解析 XML：限制大小，禁用外部实体（防 XXE）。"""
    if len(xml_text) > max_size:
        log.warning(f'XML 内容过大 ({len(xml_text)} bytes)，跳过')
        return None
    # 剥离 DOCTYPE / ENTITY 声明以防 XXE
    cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', xml_text, flags=re.IGNORECASE)
    cleaned = re.sub(r'<!ENTITY[^>]*>', '', cleaned, flags=re.IGNORECASE)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError:
        return None


def parse_rss(xml_text):
    """解析 RSS XML → list of {title, desc, link, pub_date, image}"""
    items = []
    try:
        root = _safe_parse_xml(xml_text)
        if root is None:
            return items
        # RSS 2.0
        ns = {'media': 'http://search.yahoo.com/mrss/'}
        for item in root.findall('.//item')[:8]:
            def get(tag):
                el = item.find(tag)
                return (el.text or '').strip() if el is not None else ''
            title = get('title')
            desc  = re.sub(r'<[^>]+>', '', get('description'))[:200]
            link  = get('link')
            pub   = get('pubDate')
            # 图片
            img = ''
            enc = item.find('enclosure')
            if enc is not None and 'image' in (enc.get('type') or ''):
                img = enc.get('url', '')
            media = item.find('media:thumbnail', ns) or item.find('media:content', ns)
            if media is not None:
                img = media.get('url', img)
            items.append({'title': title, 'desc': desc, 'link': link,
                          'pub_date': pub, 'image': img})
    except Exception:
        pass
    return items

def match_category(item, category):
    """判断新闻是否属于该分类（用于军事/AI过滤）"""
    kws = CATEGORY_KEYWORDS.get(category, [])
    if not kws:
        return True
    text = (item['title'] + ' ' + item['desc']).lower()
    return any(k in text for k in kws)

def fetch_category(category, feeds, max_items=5):
    """抓取一个分类的新闻"""
    seen_urls = set()
    results = []
    for source_name, url in feeds:
        if len(results) >= max_items:
            break
        xml = curl_rss(url)
        if not xml:
            continue
        items = parse_rss(xml)
        for item in items:
            if not item['title']:
                continue
            if item['link'] in seen_urls:
                continue
            # 军事和AI分类需要关键词过滤
            if category in CATEGORY_KEYWORDS and not match_category(item, category):
                continue
            seen_urls.add(item['link'])
            results.append({
                'title': item['title'],
                'summary': item['desc'] or item['title'],
                'link': item['link'],
                'pub_date': item['pub_date'],
                'image': item['image'],
                'source': source_name,
            })
            if len(results) >= max_items:
                break
    return results

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='强制采集，忽略幂等锁')
    args = parser.parse_args()

    # 幂等锁：防重复执行
    today = datetime.date.today().strftime('%Y%m%d')
    lock_file = DATA / f'morning_brief_{today}.lock'
    if lock_file.exists() and not args.force:
        age = datetime.datetime.now().timestamp() - lock_file.stat().st_mtime
        if age < 3600:  # 1小时内不重复
            log.info(f'今日已采集（{today}），跳过（使用 --force 强制采集）')
            return
    # 注意：lock 放到采集成功后再 touch，防止失败也锁定

    # 读取用户配置
    config_file = DATA / 'morning_brief_config.json'
    config = {}
    try:
        config = json.loads(config_file.read_text())
    except Exception:
        pass

    # 已启用的分类
    enabled_cats = set()
    if config.get('categories'):
        for c in config['categories']:
            if c.get('enabled', True):
                enabled_cats.add(c['name'])
    else:
        enabled_cats = set(FEEDS.keys())

    # 用户自定义关键词（全局加权）
    user_keywords = [kw.lower() for kw in config.get('keywords', [])]

    # 合并自定义 RSS 源
    custom_feeds = config.get('custom_feeds', [])
    merged_feeds = {}
    for cat, feeds in FEEDS.items():
        if cat in enabled_cats:
            merged_feeds[cat] = list(feeds)
    for cf in custom_feeds:
        cat = cf.get('category', '')
        feed_url = cf.get('url', '')
        if cat in enabled_cats and feed_url:
            # 校验自定义源 URL（SSRF 防护）
            if validate_url(feed_url):
                merged_feeds.setdefault(cat, []).append((cf.get('name', '自定义'), feed_url))
            else:
                log.warning(f'自定义源 URL 不合法，跳过: {feed_url}')

    log.info(f'开始采集 {today}...')
    log.info(f'  启用分类: {", ".join(enabled_cats)}')
    if user_keywords:
        log.info(f'  关注词: {", ".join(user_keywords)}')
    if custom_feeds:
        log.info(f'  自定义源: {len(custom_feeds)} 个')

    result = {
        'date': today,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'categories': {}
    }

    for category, feeds in merged_feeds.items():
        log.info(f'  采集 {category}...')
        items = fetch_category(category, feeds)
        # Boost items matching user keywords
        if user_keywords:
            for item in items:
                text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
                item['_kw_hits'] = sum(1 for kw in user_keywords if kw in text)
            items.sort(key=lambda x: x.get('_kw_hits', 0), reverse=True)
            for item in items:
                item.pop('_kw_hits', None)
        result['categories'][category] = items
        log.info(f'    {category}: {len(items)} 条')

    # 写入今日文件
    today_file = DATA / f'morning_brief_{today}.json'
    atomic_json_write(today_file, result)

    # 覆写 latest（看板读这个）
    latest_file = DATA / 'morning_brief.json'
    atomic_json_write(latest_file, result)

    total = sum(len(v) for v in result['categories'].values())
    log.info(f'✅ 完成：共 {total} 条新闻 → {today_file.name}')

    # 采集成功后才写入幂等锁
    lock_file.touch()

if __name__ == '__main__':
    main()
