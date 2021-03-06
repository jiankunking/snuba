import json
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar


TEncoded = TypeVar("TEncoded")

TDecoded = TypeVar("TDecoded")


class Codec(Generic[TEncoded, TDecoded], ABC):
    @abstractmethod
    def encode(self, value: TDecoded) -> TEncoded:
        raise NotImplementedError

    @abstractmethod
    def decode(self, value: TEncoded) -> TDecoded:
        raise NotImplementedError


T = TypeVar("T")


class PassthroughCodec(Generic[T], Codec[T, T]):
    def encode(self, value: T) -> T:
        return value

    def decode(self, value: T) -> T:
        return value


class JSONCodec(Codec[str, Any]):
    def encode(self, value: Any) -> str:
        return json.dumps(value)

    def decode(self, value: str) -> Any:
        return json.loads(value)
