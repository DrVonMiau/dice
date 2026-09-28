from gi.repository import GObject


class Game(GObject.Object):
    __gtype_name__ = "DiceGame"
    id = GObject.Property(type=int, default=0)
    path = GObject.Property(type=str, default="")
    platform = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    region = GObject.Property(type=str, default="")
    size = GObject.Property(type=GObject.TYPE_INT64, default=0)
    cover_path = GObject.Property(type=str, default="")
    favorite = GObject.Property(type=bool, default=False)
    added_at = GObject.Property(type=float, default=0.0)
    last_played = GObject.Property(type=float, default=0.0)
