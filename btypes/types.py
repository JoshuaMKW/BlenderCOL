import struct as _struct


class BasicType:

    def __init__(self, formatCharacter, endianess):
        self.formatCharacter = formatCharacter
        self.endianess = endianess
        self.format_string = endianess + formatCharacter
        self.size = _struct.calcsize(self.format_string)

    def pack(self, stream, value):
        stream.write(_struct.pack(self.format_string, value))

    def unpack(self, stream):
        return _struct.unpack(self.format_string, stream.read(self.size))[0]

    def sizeof(self):
        return self.size


class FixedPointConverter:

    def __init__(self, integerType, scale):
        self.integerType = integerType
        self.scale = scale

    def pack(self, stream, value):
        self.integerType.pack(stream, int(value/self.scale))

    def unpack(self, stream):
        return self.integerType.unpack(stream)*self.scale

    def sizeof(self):
        return self.integerType.sizeof()


class ByteString:

    def __init__(self, length):
        self.length = length

    def pack(self, stream, string):
        if len(string) != self.length:
            raise ValueError("wrong string length")
        stream.write(string)

    def unpack(self, stream):
        return stream.read(self.length)

    def sizeof(self):
        return self.length


class Array:

    def __init__(self, elementType, length):
        self.elementType = elementType
        self.length = length

    def pack(self, stream, array):
        if len(array) != self.length:
            raise ValueError("wrong array length")
        for value in array:
            self.elementType.pack(stream, value)

    def unpack(self, stream):
        return [self.elementType.unpack(stream) for i in range(self.length)]

    def sizeof(self):
        return self.length*self.elementType.sizeof()


class CString:

    def __init__(self, encoding):
        self.encoding = encoding

    def pack(self, stream, string):
        stream.write((string + "\0").encode(self.encoding))

    def unpack(self, stream):
        # XXX: This might not work for all encodings
        null = "\0".encode(self.encoding)
        string = b""
        while True:
            c = stream.read(len(null))
            if c == null:
                break
            string += c
        return string.decode(self.encoding)

    def sizeof(self):
        return None


class PString:

    def __init__(self, lengthType, encoding):
        self.lengthType = lengthType
        self.encoding = encoding

    def pack(self, stream, string):
        string = string.encode(self.encoding)
        self.lengthType.pack(stream, len(string))
        stream.write(string)

    def unpack(self, stream):
        length = self.lengthType.unpack(stream)
        return stream.read(length).decode(self.encoding)

    def sizeof(self):
        return None


class Field:

    def __init__(self, name, fieldType):
        self.name = name
        self.fieldType = fieldType

    def pack(self, stream, struct):
        self.fieldType.pack(stream, getattr(struct, self.name))

    def unpack(self, stream, struct):
        setattr(struct, self.name, self.fieldType.unpack(stream))

    def sizeof(self):
        return self.fieldType.sizeof()

    def equal(self, struct, other):
        return getattr(struct, self.name) == getattr(other, self.name)


class Padding(object):

    def __init__(self, length, padding=b"\xFF"):
        self.length = length
        self.padding = padding

    def pack(self, stream, struct):
        stream.write(self.padding*self.length)

    def unpack(self, stream, struct):
        stream.read(self.length)

    def sizeof(self):
        return self.length

    def equal(self, struct, other):
        return True


class StructClassDictionary(dict):

    def __init__(self):
        super().__init__()
        self.structFields = []

    def __setitem__(self, key, value):
        if not key[:2] == key[-2:] == "__" and not hasattr(value, "__get__"):
            self.structFields.append(Field(key, value))
        elif key == "__padding__":
            self.structFields.append(value)
        else:
            super().__setitem__(key, value)


class StructMetaClass(type):

    @classmethod
    def __prepare__(metacls, cls, bases):
        return StructClassDictionary()

    def __new__(metacls, cls, bases, classdict):
        if any(field.sizeof() is None for field in classdict.structFields):
            structSize = None
        else:
            structSize = sum(field.sizeof()
                              for field in classdict.structFields)

        structClass = type.__new__(metacls, cls, bases, classdict)
        structClass.structFields = classdict.structFields
        structClass.structSize = structSize
        return structClass

    def __init__(self, cls, bases, classdict):
        super().__init__(cls, bases, classdict)


class Struct(metaclass=StructMetaClass):

    __slots__ = tuple()

    def __eq__(self, other):
        return all(field.equal(self, other) for field in self.structFields)

    @classmethod
    def pack(cls, stream, struct):
        for field in cls.structFields:
            field.pack(stream, struct)

    @classmethod
    def unpack(cls, stream):
        # TODO: what if __init__ does something important?
        struct = cls.__new__(cls)
        for field in cls.structFields:
            field.unpack(stream, struct)
        return struct

    @classmethod
    def sizeof(cls):
        return cls.structSize
