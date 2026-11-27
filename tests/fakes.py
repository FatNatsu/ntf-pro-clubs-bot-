"""Stand-ins for discord.py, just enough to run the NTF session code end to end."""
import sys, types, itertools
_ids = itertools.count(1000)

class HTTPException(Exception):
    status = 0; text = ""
class Forbidden(HTTPException): pass
class NotFound(HTTPException): pass
class DiscordServerError(HTTPException): pass

class _Enum:
    def __getattr__(self, n): return n
class Color:
    def __init__(self, n="c"): self.n = n
    @staticmethod
    def _mk(n): return staticmethod(lambda: Color(n))
for _n in ["blurple","orange","green","red","gold","dark_grey","blue","greyple"]:
    setattr(Color, _n, staticmethod(lambda _n=_n: Color(_n)))

class Embed:
    def __init__(self, title=None, description=None, color=None, colour=None):
        self.title, self.description, self.colour = title, description, color or colour
        self.fields = []; self.footer = None; self.image = None
    def add_field(self, name, value, inline=True): self.fields.append((name, value)); return self
    def set_footer(self, text=None): self.footer = text; return self
    def set_thumbnail(self, url=None): return self
    def set_image(self, url=None): self.image = url; return self

class File:
    def __init__(self, path, filename=None): self.path, self.filename = path, filename
class PermissionOverwrite:
    def __init__(self, **k): self.k = dict(k)
    def __getattr__(self, n):
        if n == "k": raise AttributeError(n)
        return self.k.get(n)
class SelectOption:
    def __init__(self, label=None, value=None, **k): self.label, self.value = label, value

class Item:
    callback = None
class Button(Item):
    def __init__(self, label=None, style=None, emoji=None, row=None, disabled=False, **k):
        self.label, self.style, self.emoji, self.disabled = label, style, emoji, disabled
class Select(Item):
    def __init__(self, placeholder=None, options=None, **k):
        self.placeholder, self.options, self.values = placeholder, options or [], []
class View:
    def __init__(self, timeout=None): self.children = []
    def add_item(self, i): self.children.append(i); return self
    def button(self, label):
        return next(c for c in self.children if isinstance(c, Button) and c.label and label in c.label)
class Modal:
    def __init_subclass__(cls, **k): pass
    def __init__(self, *a, **k): self.children = []
    def add_item(self, i): self.children.append(i)
class TextInput:
    def __init__(self, **k): self.value = k.get("default", "")
def _deco(*a, **k):
    def w(f):
        try:
            f.autocomplete = lambda *a2, **k2: (lambda g: g)  # slash-command options
        except AttributeError:
            pass
        return f
    return w

async def _net():
    # Real discord.py calls go over the network, so they suspend the task.
    # Yielding here reproduces that, which matters for cancellation and races.
    if NET_YIELD:
        import asyncio
        await asyncio.sleep(0)

NET_YIELD = True
BOT_ID = 4242
class _User:
    def __init__(self, uid): self.id = uid

class Message:
    def __init__(self, channel, content=None, embed=None, embeds=None, view=None):
        self.id = next(_ids); self.channel = channel; self.content = content
        self.embeds = list(embeds) if embeds else ([embed] if embed else [])
        self.view = view; self.deleted = False; self.pinned = False; self.author = _User(BOT_ID)
    async def edit(self, **k):
        await _net()
        if self.deleted: raise NotFound()
        if "embed" in k: self.embeds = [k["embed"]]
        if "embeds" in k: self.embeds = list(k["embeds"])
        if "view" in k: self.view = k["view"]
        if "content" in k: self.content = k["content"]
    async def delete(self):
        await _net()
        self.deleted = True
        if self in self.channel.messages: self.channel.messages.remove(self)
    async def pin(self): self.pinned = True
    async def unpin(self): self.pinned = False

class Channel:
    def __init__(self, guild, name, kind, category=None, overwrites=None, user_limit=0):
        self.id = next(_ids); self.guild = guild; self.name = name; self.kind = kind
        self.category_id = category.id if category else None
        self.overwrites = dict(overwrites or {}); self.user_limit = user_limit
        self.messages = []; self.members = []; self.deleted = False; self.renames = 0
        self.mention = f"<#{self.id}>"
    @property
    def channels(self): return [c for c in self.guild.channels.values() if c.category_id == self.id]
    async def send(self, content=None, embed=None, embeds=None, view=None, file=None):
        await _net()
        if self.deleted: raise NotFound()
        m = Message(self, content, embed, embeds, view); m.file = file; self.messages.append(m); return m
    async def edit(self, **k):
        if "name" in k: self.name = k["name"]; self.renames += 1
        if "category" in k: self.category_id = k["category"].id
        if "user_limit" in k: self.user_limit = k["user_limit"]
    async def set_permissions(self, target, overwrite=..., **k):
        if overwrite is None: self.overwrites.pop(target, None)
        else: self.overwrites[target] = PermissionOverwrite(**k)
    async def delete(self, reason=None):
        self.deleted = True; self.guild.channels.pop(self.id, None)
    async def purge(self, limit=100, check=None):
        await _net()
        if limit is None: limit = len(self.messages)
        d = [m for m in self.messages[:limit] if check is None or check(m)]
        for m in d: m.deleted = True
        self.messages = [m for m in self.messages if m not in d]; return d
    async def fetch_message(self, mid):
        for m in self.messages:
            if m.id == mid: return m
        raise NotFound()
    def can_see(self, member):
        ow = self.overwrites.get(member)
        if ow and "view_channel" in ow.k: return ow.k["view_channel"]
        ev = self.overwrites.get(self.guild.default_role)
        return ev.k.get("view_channel", True) if ev else True

class Perm:
    def __init__(self, admin): self.manage_guild = admin
class Voice:
    def __init__(self, ch, member=None): self.channel = ch; self._member = member
    @property
    def mute(self): return bool(self._member and self._member.muted)  # server mute follows the member
class Member:
    def __init__(self, guild, uid, admin=False):
        self.id = uid; self.guild = guild; self.guild_permissions = Perm(admin)
        self.display_name = f"P{uid}"; self.mention = f"<@{uid}>"; self.bot = False
        self.voice = None; self.muted = False
        self.display_avatar = types.SimpleNamespace(url="x")
    def __hash__(self): return hash(("m", self.id))
    def __eq__(self, o): return isinstance(o, Member) and o.id == self.id
    def join(self, ch):
        if self.voice and self.voice.channel and self in self.voice.channel.members:
            self.voice.channel.members.remove(self)
        self.voice = Voice(ch, self) if ch else None
        if ch: ch.members.append(self)
    async def move_to(self, ch): self.join(ch)
    async def edit(self, mute=None, **k):
        if mute is not None: self.muted = mute

class Guild:
    def __init__(self, gid=1):
        self.id = gid; self.channels = {}; self.members = {}
        self.default_role = "everyone"; self.me = Member(self, 1, admin=True)
    def add_member(self, uid, admin=False):
        m = Member(self, uid, admin); self.members[uid] = m; return m
    def get_member(self, uid): return self.members.get(uid)
    def get_channel(self, cid): return self.channels.get(cid)
    def _mk(self, name, kind, category=None, overwrites=None, user_limit=0):
        c = Channel(self, name, kind, category, overwrites, user_limit); self.channels[c.id] = c; return c
    async def create_category(self, name=None, **k): return self._mk(name, "category")
    async def create_text_channel(self, name=None, category=None, overwrites=None, **k):
        return self._mk(name, "text", category, overwrites)
    async def create_voice_channel(self, name=None, category=None, overwrites=None, user_limit=0, **k):
        return self._mk(name, "voice", category, overwrites, user_limit)

class Response:
    def __init__(self, it): self.it = it
    async def send_message(self, content=None, view=None, ephemeral=False, **k): self.it.replies.append((content, view)); self.it.embeds.append(k.get('embed'))
    async def defer(self, ephemeral=False, **k): pass
    async def edit_message(self, **k):
        if self.it.message: await self.it.message.edit(**k)
    async def send_modal(self, modal): self.it.modal = modal
class Followup:
    def __init__(self, it): self.it = it
    async def send(self, content=None, view=None, ephemeral=False, **k): self.it.replies.append((content, view))
class Interaction:
    def __init__(self, guild, user, message=None, values=None):
        self.guild, self.user, self.guild_id, self.message = guild, user, guild.id, message
        self.replies = []; self.embeds = []; self.modal = None; self.data = {"values": values or []}
        self.response, self.followup = Response(self), Followup(self)
    @property
    def last(self): return self.replies[-1][0] if self.replies else None
    @property
    def last_view(self): return self.replies[-1][1] if self.replies else None

class Bot:
    def __init__(self, guild): self.guild = guild; self.user = _User(BOT_ID)
    def get_guild(self, gid): return self.guild
    def get_user(self, uid): return self.guild.members.get(uid)
    def get_cog(self, name): return getattr(self, 'cogs', {}).get(name)
    def add_view(self, v): pass

def install():
    d = types.ModuleType("discord")
    for n, v in dict(HTTPException=HTTPException, Forbidden=Forbidden, NotFound=NotFound,
                     DiscordServerError=DiscordServerError, Color=Color, Embed=Embed,
                     PermissionOverwrite=PermissionOverwrite, SelectOption=SelectOption, File=File,
                     ButtonStyle=_Enum(), TextStyle=_Enum(), Interaction=Interaction, Member=Member,
                     Guild=Guild, VoiceState=object, VoiceChannel=Channel, CategoryChannel=Channel,
                     TextChannel=Channel).items():
        setattr(d, n, v)
    ui = types.ModuleType("discord.ui")
    ui.View, ui.Button, ui.Select, ui.Modal, ui.TextInput, ui.button, ui.select = View, Button, Select, Modal, TextInput, _deco, _deco
    d.ui = ui
    ac = types.ModuleType("discord.app_commands")
    class _Any:
        def __init__(s, *a, **k): pass
        def __call__(s, *a, **k): return _deco()
        def __getattr__(s, n): return _Any()
    def _command(*a, **k):
        def w(f):
            f.autocomplete = lambda *a2, **k2: (lambda g: g)
            f.error = lambda g: g
            return f
        return w
    ac.command = _command; ac.describe = _deco; ac.choices = _deco; ac.checks = _Any(); ac.Choice = _Any(); ac.Group = _Any
    d.app_commands = ac
    ext = types.ModuleType("discord.ext"); cm = types.ModuleType("discord.ext.commands")
    class Cog:
        @staticmethod
        def listener(*a, **k): return _deco()
    cm.Cog = Cog; cm.Bot = Bot; ext.commands = cm; d.ext = ext
    tk = types.ModuleType("discord.ext.tasks")
    class _Loop:
        def __init__(self, fn): self.fn = fn
        def __get__(self, obj, cls): return self
        def start(self, *a, **k): pass
        def cancel(self): pass
        def before_loop(self, fn): return fn
    tk.loop = lambda *a, **k: (lambda fn: _Loop(fn))
    ext.tasks = tk
    sys.modules.update({"discord": d, "discord.ui": ui, "discord.app_commands": ac,
                        "discord.ext": ext, "discord.ext.commands": cm, "discord.ext.tasks": tk})
    return d
