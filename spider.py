# -*- encoding: utf-8 -*-
'''
@File    :   douyin.py
@Time    :   2023年05月18日 19:32:17 星期四
@Author  :   erma0
@Version :   3.2
@Link    :   https://erma0.cn
@Desc    :   抖音爬虫
'''
import os
import re
from typing import List
from urllib.parse import quote, unquote, urlparse

import ujson as json
from loguru import logger
from playwright.sync_api import Route, TimeoutError

from browser import Browser, BrowserContext


class Douyin(object):

    def __init__(self, context: BrowserContext, url: str, num: int = -1, type: str = 'post'):
        """
        初始化
        type=['post', 'like', 'music', 'search', 'follow', 'fans', 'collection', 'video']
        """
        self.context = context
        self.url = url.strip()
        self.num = num
        self.type = type
        self.has_more = True
        self.down_path = os.path.join('.', '下载')
        if not os.path.exists(self.down_path): os.makedirs(self.down_path)
        self.pageDown = 0
        self.pageDownMax = 5  # 重试次数
        self.results = []  # 保存结果
        self.results_old = []  # 前一次保存结果
        self.hookURL_init()  # 初始化参数
        self.page_options()  # 打开页面后的操作

    @staticmethod
    def str2path(str: str):
        """
        把字符串转为Windows合法文件名
        """
        # 非法字符
        lst = ['\r', '\n', '\\', '/', ':', '*', '?', '"', '<', '>', '|', ' ']
        # 非法字符处理方式1
        for key in lst:
            str = str.replace(key, '_')
        # 非法字符处理方式2
        # str = str.translate(None, ''.join(lst))
        # 文件名+路径长度最大255，汉字*2，取80
        if len(str) > 80:
            str = str[:80]
        return str.strip()

    @staticmethod
    def quit(str):
        """
        直接退出程序
        """
        logger.error(str)
        exit()

    def url2redirect(self, url):
        """
        取302跳转地址
        """
        # a = self.context.request.head(url)
        r = self.context.new_page()
        r.route("**/*", lambda route: route.abort() if route.request.resource_type != "document" else route.continue_())
        r.goto(url, wait_until='domcontentloaded')
        url = r.url
        r.close()
        return url

    @staticmethod
    def filter_emoji(desstr, restr=''):
        # 过滤表情，在处理文件名的时候如果想去除emoji可以调用
        try:
            res = re.compile(u'[\U00010000-\U0010ffff]')
        except re.error:
            res = re.compile(u'[\uD800-\uDBFF][\uDC00-\uDFFF]')
        return res.sub(restr, desstr)

    def _append_users(self, user_list: List[dict]):
        if not user_list:
            logger.error("本次请求结果为空")
            return
        if self.num < 0 or len(self.results) < self.num:
            for item in user_list:
                if self.num > 0 and len(self.results) >= self.num:
                    self.has_more = False
                    logger.info(f'已达到限制采集数量：{len(self.results)}')
                    return
                info = {}
                info['nickname'] = self.str2path(item['nickname'])
                info['signature'] = self.str2path(item['signature'])
                info['avatar'] = item['avatar_larger']['url_list'][0]
                for i in [
                        'sec_uid', 'uid', 'short_id', 'unique_id', 'unique_id_modify_time', 'aweme_count', 'favoriting_count',
                        'follower_count', 'following_count', 'constellation', 'create_time', 'enterprise_verify_reason',
                        'is_gov_media_vip', 'live_status', 'total_favorited', 'share_qrcode_uri'
                ]:
                    if item.get(i):
                        info[i] = item[i]
                room_id = item.get('room_id')
                if room_id:  # 直播间
                    info['live_room_id'] = room_id
                    info['live_room_url'] = [
                        f'http://pull-flv-f26.douyincdn.com/media/stream-{room_id}.flv',
                        f'http://pull-hls-f26.douyincdn.com/media/stream-{room_id}.m3u8'
                    ]
                music_count = item['original_musician']['music_count']
                if music_count:  # 原创音乐人
                    info['original_musician'] = item['original_musician']

                self.results.append(info)  # 用于保存信息
            logger.info(f'采集中，已采集到{len(self.results)}条结果')
        else:
            self.has_more = False
            logger.info(f'已达到限制采集数量：{len(self.results)}')

    def _append_awemes(self, aweme_list: List[dict]):
        """
        数据入库
        """
        if not aweme_list:
            logger.error("本次请求结果为空")
            return
        if self.num < 0 or len(self.results) < self.num:
            for item in aweme_list:
                if self.num > 0 and len(self.results) >= self.num:
                    self.has_more = False
                    logger.info(f'已达到限制采集数量：{len(self.results)}')
                    return
                # =====下载视频=====
                _time = item.get('create_time', item.get('createTime'))
                _is_top = item.get('is_top', item.get('tag', {}).get('isTop'))
                if self.results_old:
                    old = self.results_old[0]['time']
                    if _time <= old:  # 如果当前作品时间早于上次采集的最新作品时间，且不是置顶作品，直接退出
                        if _is_top:
                            continue
                        self.has_more = False
                        logger.success(f'增量采集完成，上次运行结果：{old}')
                        return
                _type = item.get('aweme_type', item.get('awemeType'))
                info = item.get('statistics', item.get('stats', {}))
                for i in [
                        'playCount', 'downloadCount', 'forwardCount', 'collectCount', "digest", "exposure_count",
                        "live_watch_count", "play_count", "download_count", "forward_count", "lose_count", "lose_comment_count"
                ]:
                    if not info.get(i):
                        info.pop(i, '')
                info.pop('aweme_id', '')
                if _type <= 66 or _type == 107:  # 视频 77西瓜视频
                    play_addr = item['video'].get('play_addr')
                    if play_addr:
                        download_addr = item['video']['play_addr']['url_list'][-1]
                    else:
                        download_addr = f"https:{ item['video']['playApi']}"
                    info['download_addr'] = download_addr
                elif _type == 68:  # 图文
                    info['download_addr'] = [images.get('url_list', images.get('urlList'))[-1] for images in item['images']]
                elif _type == 101:  # 直播
                    continue
                else:  # 其他类型作品
                    info['download_addr'] = '其他类型作品'
                    logger.info('type', _type)
                    with open(f'{_type}.json', 'w', encoding='utf-8') as f:  # 保存未区分的类型
                        json.dump(item, f, ensure_ascii=False)  # 中文不用Unicode编码
                    continue
                info['id'] = item.get('aweme_id', item.get('awemeId'))
                info['time'] = _time
                desc = self.str2path(item.get('desc'))
                info['desc'] = desc
                music = item.get('music')
                if music:
                    info['music_title'] = self.str2path(music['title'])
                    info['music_url'] = music.get('play_url', music.get('playUrl'))['uri']
                cover = item['video'].get('origin_cover')
                if cover:
                    info['cover'] = item['video']['origin_cover']['url_list'][-1]
                else:
                    info['cover'] = f"https:{item['video']['originCover']}"
                tags = item.get('text_extra', item.get('textExtra'))
                if tags:
                    info['tags'] = [{
                        'tag_id': hashtag.get('hashtag_id', hashtag.get('hashtagId')),
                        'tag_name': hashtag.get('hashtag_name', hashtag.get('hashtagName'))
                    } for hashtag in tags]
                self.results.append(info)  # 用于保存信息
            logger.info(f'采集中，已采集到{len(self.results)}条结果')
        else:
            self.has_more = False
            logger.info(f'已达到限制采集数量：{len(self.results)}')

    def download(self):
        """
        采集完成后，统一下载已采集的结果
        """
        if os.path.exists(self.aria2_conf):
            logger.info('开始下载')
            command = f"aria2c -c --console-log-level warn -d {self.down_path} -i {self.aria2_conf}"
            os.system(command)  # system有输出，阻塞
            # os.popen(command)  # popen无输出，不阻塞
        else:
            logger.error('没有发现可下载的配置文件')

    def save(self):
        if self.results:
            logger.success(f'采集完成，本次共采集到{len(self.results)}条结果')
            if self.type in ['post', 'like', 'music', 'search', 'collection', 'video']:  # 视频列表保存为Aria下载文件
                with open(self.aria2_conf, 'w', encoding='utf-8') as f:
                    _ = []
                    for line in self.results:  # 只保存本次采集结果的下载配置
                        filename = f'{line["id"]}_{line["desc"]}'
                        if isinstance(line["download_addr"], list):
                            down_path = self.down_path.replace(line["id"], filename) if self.type == 'video' else os.path.join(
                                self.down_path, filename)
                            [
                                _.append(f'{addr}\n\tdir={down_path}\n\tout={line["id"]}_{index + 1}.jpeg\n')
                                for index, addr in enumerate(line["download_addr"])
                            ]
                        elif isinstance(line["download_addr"], str):
                            _.append(f'{line["download_addr"]}\n\tdir={self.down_path}\n\tout={filename}.mp4\n')
                        else:
                            logger.error("下载地址错误")
                    f.writelines(_)
            with open(f'{self.down_path}.json', 'w', encoding='utf-8') as f:  # 保存所有数据到文件，包括旧数据
                if self.type == 'post':  # 除主页作品外都不需要按时间排序
                    self.results.sort(key=lambda item: item['id'], reverse=True)
                    self.results.extend(self.results_old)
                json.dump(self.results, f, ensure_ascii=False)
        else:
            logger.error("本次采集结果为空")

    def handle(self, route: Route):
        if self.has_more:
            if self.pageDown > 0:
                self.pageDown = 0
            response = route.fetch()
            try:
                resj = response.json()
                self.has_more = resj.get('has_more', True)
                if self.type == 'follow':
                    info = resj.get('followings')
                    self._append_users(info)
                elif self.type == 'fans':
                    info = resj.get('followers')
                    self._append_users(info)
                elif self.type == 'search':
                    info = []
                    for item in resj.get('data'):
                        if item['type'] == 1:  # 1作品 16合集 76百科 77头条文章 996热榜 997微头条
                            _info = item['aweme_info']
                            info.append(_info)
                        elif item['type'] == 16:
                            _info = item['aweme_mix_info']['mix_items']
                            info.extend(_info)
                        elif item['type'] == 996:
                            _info = item['sub_card_list'][0]['hotspot_info']['hotspot_items']
                            info.extend(_info)
                        else:
                            pass
                    self._append_awemes(info)
                else:
                    info = resj.get('aweme_list')
                    self._append_awemes(info)
            except Exception as err:
                logger.error(f'err：  {err}')
                with open('error.json', 'w', encoding='utf-8') as f:  # 保存未区分的类型
                    json.dump(response.json(), f, ensure_ascii=False)  # 中文不用Unicode编码
        route.fulfill(response=response)

    def hookURL_init(self):
        if urlparse(self.url).netloc.endswith('douyin.com'):  # 链接
            if self.url.endswith('?showTab=like'): self.type = 'like'
            self.url = self.url2redirect(self.url)
        elif self.url.isdigit():  # 数字ID，作品
            self.url = f'https://www.douyin.com/video/{self.url}'
        else:  # 关键字，搜索
            self.url = f'https://www.douyin.com/search/{quote(self.url)}'
        *_, _type, self.id = unquote(urlparse(self.url).path.strip('/')).split('/')
        hookURL = '/aweme/v[123]/web/'
        if _type in ['video', 'note']:  # 自动识别 单个作品 video
            self.type = 'video'
            hookURL = '单个作品无需hookURL'
        if _type == 'search':  # 自动识别 搜索
            self.type = 'search'
            hookURL += 'general/search'
        elif _type == 'music':  # 自动识别 音乐
            self.type = 'music'
            hookURL += 'music'
        elif _type == 'collection':  # 自动识别 合集
            self.type = 'collection'
            hookURL += 'mix/aweme'
        elif self.type == 'post':
            hookURL += 'aweme/post'
        elif self.type == 'like':
            hookURL += 'aweme/favorit'
            if not self.url.endswith('showTab=like'):
                self.url = self.url.split('?')[0] + '?showTab=like'
        elif self.type == 'follow':
            hookURL += 'user/following'
        elif self.type == 'fans':
            hookURL += 'user/follower'
        else:  # 备用
            pass
        self.hookURL = re.compile(hookURL, re.S)

    def page_options(self):
        self.page = self.context.new_page()
        self.page.set_default_timeout(0)
        self.page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
        self.page.route(self.hookURL, self.handle)
        self.page.goto(self.url)

        render_data = json.loads(unquote(self.page.locator('id=RENDER_DATA').inner_text()))
        self.render_data = render_data[sorted(render_data.keys())[1]]
        if self.type in ['post', 'like', 'follow', 'fans']:
            self.info = self.render_data['user']  # 备用
            self.title = self.info['user']['nickname']
            if self.type == 'follow':  # 点击关注列表
                self.page.locator('[data-e2e="user-info-follow"]').click()
                self.page.locator('[data-e2e="user-fans-container"]').click()
            elif self.type == 'fans':  # 点击粉丝列表
                self.page.locator('[data-e2e="user-info-fans"]').click()
                self.page.locator('[data-e2e="user-fans-container"]').click()
        elif self.type == 'search':
            self.title = self.id
            self.info = self.render_data['defaultSearchParams']
            # self.title = self.info['keyword']
        elif self.type == 'collection':
            self.info = self.render_data['aweme']['detail']['mixInfo']
            self.title = self.info['mixName']
        elif self.type == 'music':  # 聚焦滚动列表
            self.info = self.render_data['musicDetail']
            self.title = self.info['title']
            self.page.locator('[data-e2e="scroll-list"]').last.click()
        elif self.type == 'video':
            self.title = self.id
            # self.title = '单个作品'
        else:  # 备用
            pass

        self.down_path = os.path.join(self.down_path, self.str2path(f'{self.type}_{self.title}'))
        self.aria2_conf = f'{self.down_path}.txt'

    def page_next(self):  # 加载数据
        if self.type == 'collection':
            self.page.get_by_role("button", name="点击加载更多").click()
        else:
            self.page.keyboard.press('End')
        # logger.info("加载中")

    def run(self):
        """
        开始采集
        """
        if self.type == 'post':  # post页面需提取初始页面数据，先得到下载目录后再提取
            if os.path.exists(f'{self.down_path}.json'):  # 主页作品可以增量采集，其他类型难以实现
                with open(f'{self.down_path}.json', 'r', encoding='utf-8') as f:
                    self.results_old = json.load(f)
            # 从新到旧排序,无视置顶作品（此需求一般用来采集最新作品）
            render_data_ls = self.render_data['post']['data']
            render_data_ls.sort(key=lambda item: item.get('aweme_id', item.get('awemeId')), reverse=True)
            self._append_awemes(render_data_ls)
        elif self.type == 'video':  # video页面需提取初始页面数据
            render_data_ls = [self.render_data['aweme']['detail']]
            self._append_awemes(render_data_ls)
            self.has_more = False
        else:  # 备用
            pass

        while self.has_more and self.pageDown <= self.pageDownMax:
            try:
                with self.page.expect_request_finished(lambda request: self.hookURL.search(request.url), timeout=2000):
                    self.page_next()  # 加载下一批数据
            except TimeoutError:  # 重试
                self.pageDown += 1
                logger.error("重试 + 1")
        self.page.unroute(self.hookURL, self.handle)
        self.save()  # 保存结果
        # self.page.wait_for_timeout(1000)
        # self.page.screenshot(path="end.png")
        self.page.close()


def test():
    edge = Browser()

    # a = Douyin(context=edge.context,url='https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4', num=11)  # 作品
    # a = Douyin(context=edge.context,url='https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4')  # 作品
    # a = Douyin(context=edge.context, url='https://v.douyin.com/UhYnoMS/')  # 单个作品
    # a = Douyin(context=edge.context, url='7233251303269453089')  # 单个作品 ID图文
    # a = Douyin(context=edge.context,url='https://v.douyin.com/BK2VMkG/')  # 图集主页
    # a = Douyin(context=edge.context,url='https://v.douyin.com/BGPBena/', type='music')  # 音乐
    # a = Douyin(context=edge.context,url='https://v.douyin.com/BGPBena/', num=11)  # 音乐
    # a = Douyin(context=edge.context,url='https://www.douyin.com/search/%E4%B8%8D%E8%89%AF%E4%BA%BA', num=30)  # 搜索
    # a = Douyin(context=edge.context,url='https://www.douyin.com/search/%E4%B8%8D%E8%89%AF%E4%BA%BA', type='search')  # 搜索
    # a = Douyin(context=edge.context,url='不良人', num=11)  # 关键字搜索
    # a = Douyin(context=edge.context,url='不良人', type='search', num=11)  # 关键字搜索
    # a = Douyin(context=edge.context,url='https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4?showTab=like')  # 长链接+喜欢
    # a = Douyin(context=edge.context,url='https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4', type='like')  # 长链接+喜欢
    a = Douyin(context=edge.context, url='https://v.douyin.com/BGf3Wp6/', type='like')  # 短链接+喜欢+自己的私密账号需登录
    # a = Douyin(context=edge.context,url='https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4', type='fans')  # 粉丝
    # a = Douyin(context=edge.context, url='https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4',type='follow')  # 关注
    # a = Douyin(context=edge.context,url='https://www.douyin.com/collection/7018087406876231711')  # 合集
    # a = Douyin(context=edge.context,url='https://www.douyin.com/collection/7018087406876231711', type='collect')  # 合集
    a.run()
    # a.download()
    # python ./douyin.py -u https://v.douyin.com/BGf3Wp6/ -t like

    edge.stop()


banner = '''
    ____                    _         ____        _     _           
|  _ \  ___  _   _ _   _(_)_ __   / ___| _ __ (_) __| | ___ _ __ 
| | | |/ _ \| | | | | | | | '_ \  \___ \| '_ \| |/ _` |/ _ \ '__|
| |_| | (_) | |_| | |_| | | | | |  ___) | |_) | | (_| |  __/ |   
|____/ \___/ \__,_|\__, |_|_| |_| |____/| .__/|_|\__,_|\___|_|   
                    |___/                |_|                      
                                V3.2
                Github: https://github.com/erma0/douyin
'''
print(banner)

if __name__ == "__main__":
    test()
