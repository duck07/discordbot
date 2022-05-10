import asyncio
import functools
import itertools
import math
from requests import request
import random
import discord
import youtube_dl
from async_timeout import timeout
from discord.ext import commands

youtube_dl.utils.bug_reports_message = lambda: ''


# Библиотека YTDL, нужна нам для проигрывания песен с сайта youtube.com


class YtDLibrary(discord.PCMVolumeTransformer):
    ytdl_opts = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    # ffmpeg для воспроизведения песен ботом в дискорде

    ffmpeg_opts = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = youtube_dl.YoutubeDL(ytdl_opts)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        # Инициализация всей информации (запросчик, канал, инфа о видео

        self.author = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.rename_duration(int(data.get('duration')))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return '**{0.title}** by **{0.uploader}**'.format(self)

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Не смог найти совпадений с запросом`{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Не смог найти совпадений с запросом `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Ошибка в ссылке `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Не смог найти совпадений с запросом`{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.ffmpeg_opts), data=info)

    # Это нужно для показа длительности песни (дни, часы, сек)

    @staticmethod
    def rename_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if 1 < int(repr(days)[-1]) < 5:
            duration.append('{} дня'.format(days))
        elif int(repr(days)[-1]) == 1:
            duration.append('{} день'.format(days))
        elif 5 < int(repr(days)[-1]) < 21 or int(repr(days)[-1]) == 0:
            duration.append('{} дней'.format(days))

        if int(repr(hours)[-1]) == 1:
            duration.append('{} час'.format(hours))
        elif 1 < int(repr(hours)[-1]) < 5:
            duration.append('{} часа'.format(hours))
        elif 5 < int(repr(hours)[-1]) < 21:
            duration.append('{} часов'.format(hours))

        if int(repr(minutes)[-1]) == 1:
            duration.append('{} минута'.format(minutes))
        elif 1 < int(repr(minutes)[-1]) < 5:
            duration.append('{} минуты'.format(minutes))
        elif 5 < int(repr(minutes)[-1]) < 21 or int(repr(minutes)[-1]) == 0:
            duration.append('{} минут'.format(minutes))

        if int(repr(seconds)[-1]) == 1:
            duration.append('{} секунда'.format(seconds))
        elif 1 < int(repr(seconds)[-1]) < 5:
            duration.append('{} секунды'.format(seconds))
        elif 5 < int(repr(seconds)[-1]) < 21 or int(repr(seconds)[-1]) == 0:
            duration.append('{} секунд'.format(seconds))

        return ', '.join(duration)


# Обработка и вывод информации о песне


class Song:
    __slots__ = ('source', 'author')

    def __init__(self, source: YtDLibrary):
        self.source = source
        self.author = source.author

    def create_embed(self):
        embed = discord.Embed(title='Сейчас играет:', description=f'```css\n{self.source.title}\n```',
                              color=discord.Color.teal())

        embed.add_field(name='Длительность:', value=self.source.duration)
        embed.add_field(name='Запроcил:', value=self.author.mention)
        embed.add_field(name='Автор:', value=f'[{self.source.uploader}]({self.source.uploader_url})')
        embed.add_field(name='URL:', value=f'[Нажми сюда]({self.source.url})')
        embed.set_thumbnail(url=self.source.thumbnail)

        return embed


# Очередь песни (с функциями)


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def remove(self, index: int):
        del self._queue[index]


# Cостояние бота в канале (проигрывание или нет)

class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()

            if not self.loop:
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return

            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            await self.current.source.channel.send(embed=self.current.create_embed())

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, ctx: commands.Context):

        # Голосовое состояние бота (проигрывает он музыку или нет)

        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):

        # Состояние остановки проигрывания

        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage('This command can\'t be used in DM channels.')

        return True

    async def cog_before_invoke(self, ctx: commands.Context):

        # Инициализация переменной состояния проигрывания бота

        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):

        # Ошибки

        await ctx.send('Ошибка: {}'.format(str(error)))

    @commands.command(name='join', invoke_without_subcommand=True)
    async def join(self, ctx: commands.Context):

        """Присоединяется к голосовому каналу"""

        destination = ctx.author.voice.channel
        if self.get_voice_state(ctx).voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='summon')
    async def summon(self, ctx: commands.Context, *, channel: discord.VoiceChannel = None):

        """Вызывает бота в голосовой канал. Пишите имя канала при вызове команды, или же бот подключится к каналу,
        в котором вы сейчас находитесь. """

        if not channel and not ctx.author.voice:
            raise VoiceError('Вы не в голосовом канале')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', aliases=['disconnect'])
    async def leave(self, ctx: commands.Context):

        """Очищает очередь и покидает канал"""

        if not ctx.voice_state.voice:
            return await ctx.send('Вы не в голосовом канале')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    @commands.command(name='now', aliases=['current', 'playing'])
    async def now(self, ctx: commands.Context):

        """Показывает, что играет в данный момент"""

        await ctx.send(embed=ctx.voice_state.current.create_embed())

    @commands.command(name='skip')
    async def skip(self, ctx: commands.Context):

        """Пропускает песню"""

        if not ctx.voice_state.is_playing:
            return await ctx.send('Не проигрывает музыку в данный момент.')

        await ctx.message.add_reaction('⏭')
        ctx.voice_state.skip()

    @commands.command(name='queue')
    async def queue(self, ctx: commands.Context, *, page: int = 1):
        """Очередь песен"""
        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Пустая очередь.')

        # Страницы (всего песен на странице 10)

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)
        start = (page - 1) * items_per_page
        end = start + items_per_page
        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} песенок:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                 .set_footer(text='Страница {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)

    @commands.command(name='remove')
    async def remove(self, ctx: commands.Context, index: int):

        """Убирает песню под заданным индексом"""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Пустая очередь.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.message.add_reaction('✅')

    @commands.command(name='loop')
    async def loop(self, ctx: commands.Context):

        """Зацикливает данную песню (чтобы убрать повторно напишите !loop)"""

        if not ctx.voice_state.is_playing:
            return await ctx.send('Не проигрывает музыку в данный момент.')

        # Для зацикливания и отмены команды

        ctx.voice_state.loop = not ctx.voice_state.loop
        await ctx.message.add_reaction('✅')

    @commands.command(name='play')
    async def play(self, ctx: commands.Context, *, search: str):

        """Проигрывает музыку"""

        if not ctx.voice_state.voice:
            await ctx.invoke(self.join)

        async with ctx.typing():
            try:
                source = await YtDLibrary.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send('Ошибка: {}'.format(str(e)))
            else:
                song = Song(source)

                await ctx.voice_state.songs.put(song)
                await ctx.send('Добавлена в очередь: {}'.format(str(source)))

    @join.before_invoke
    @play.before_invoke
    async def ensure_voice_state(self, ctx: commands.Context):

        # Проверка состояния бота (проигрывание, нахождение в канале)

        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('Вы не подключены к голосовому каналу')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Бот уже в голосовом канале')


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


bot = commands.Bot('!', description='Бот для музыки и разнообразных тектовых команд')
bot.add_cog(Music(bot))


@bot.event
async def on_ready():
    print('В сети:\n{0.user.name}\n{0.user.id}'.format(bot))


@bot.command(name='cat', help='котики')
async def cat(ctx):
    url = 'https://api.thecatapi.com/v1/images/search'
    html = request(method='GET',
                   url=url
                   )
    data = html.json()
    await ctx.channel.send(data[0].get('url'))


@bot.command(name='dog', help='собачки')
async def dog(ctx):
    url = 'https://dog.ceo/api/breeds/image/random'
    html = request(method='GET',
                   url=url
                   )
    data = html.json()
    await ctx.channel.send(data.get("message"))


@bot.command(name='timer', help='счетчик времени (формат h m s)')
async def timer(ctx, time1, time2, time3):
    time1 = list(time1)
    time1 = int(time1[0]) * 3600
    time2 = list(time2)
    time2 = int(time2[0]) * 60
    time3 = list(time3)
    time3 = int(time3[0])
    seconds = time1 + time2 + time3
    try:
        secondint = int(seconds)
        if secondint <= 0:
            await ctx.send("Никаких отрицательных чисел")
            raise BaseException
        message = await ctx.send("Таймер: {seconds}")
        while True:
            secondint -= 1
            if secondint == 0:
                await message.edit(content="Готово!")
                break
            await message.edit(content=f"Таймер: {secondint}")
            await asyncio.sleep(1)
        await ctx.send(f"{ctx.author.mention} Время вышло")
    except ValueError:
        await ctx.send("Должен быть формат 0h 0m 0s")


@bot.command(name='hug', help='!hug @имя пользователя, которого хотите обнять.')
async def hug(ctx, user: discord.Member = None):
    hugs = ["https://i.imgur.com/FPznEhE.gif",
            "https://i.imgur.com/PMXg5Zf.gif",
            "https://i.imgur.com/bUAuTXs.gif",
            "https://i.imgur.com/OJkfVgP.gif",
            "https://i.imgur.com/pa0R1t5.gif",
            "https://i.imgur.com/6qYOUQF.gif",
            "https://i.imgur.com/niD8tPb.gif"
            ]
    embed = discord.Embed(title="Обнимашки!!!", description=f'{ctx.author.mention} обнял(-a) <@{user.id}> 🥰',
                          color=discord.Color.blurple())
    url = random.choice(hugs)
    embed.set_image(url=url)
    await ctx.send(embed=embed)


@bot.command(name='hi', help='приветствует пользователя')
async def hi(ctx):
    await ctx.send(f"Привет {ctx.author.mention}(>'-'<)")


@bot.command(name='exit', help="Выход бота из онлайна")
@commands.has_permissions(manage_guild=True)
# Бот прекращает работу
async def bye(ctx):
    await ctx.bot.logout()


bot.run('OTY0MDc2OTg4OTIwODkzNDcx.YlfYqQ.r7n672QVrblJJ-zFSEOsWeIWh7Q')
