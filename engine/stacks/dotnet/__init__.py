"""The .NET stack: a wrapper over ``engine/dotnet_gen`` and the existing agent checks."""
from engine.stacks.dotnet.language import DotnetLanguage
from engine.stacks.dotnet.stack import DotnetStack

__all__ = ["DotnetLanguage", "DotnetStack"]
