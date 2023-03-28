import io
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    List,
    MutableSequence,
    Optional,
    Type,
    TypeVar,
    Union,
    overload,
    Dict,
)

from typing_inspect import is_union_type

from docarray.array.abstract_array import AnyDocArray
from docarray.array.array.io import IOMixinArray
from docarray.array.array.pushpull import PushPullMixin
from docarray.array.array.sequence_indexing_mixin import (
    IndexingSequenceMixin,
    IndexIterType,
)
from docarray.base_doc import AnyDoc, BaseDoc
from docarray.typing import NdArray
from pydantic import BaseModel


if TYPE_CHECKING:
    from docarray.array.stacked.array_stacked import DocArrayStacked
    from docarray.proto import DocumentArrayProto
    from docarray.typing import TorchTensor
    from docarray.typing.tensor.abstract_tensor import AbstractTensor

T = TypeVar('T', bound='DocArray')
T_doc = TypeVar('T_doc', bound=BaseDoc)


def _delegate_meth_to_data(meth_name: str) -> Callable:
    """
    create a function that mimic a function call to the data attribute of the
    DocArray

    :param meth_name: name of the method
    :return: a method that mimic the meth_name
    """
    func = getattr(list, meth_name)

    @wraps(func)
    def _delegate_meth(self, *args, **kwargs):
        return getattr(self.data, meth_name)(*args, **kwargs)

    return _delegate_meth


class DocArray(
    BaseModel,
    PushPullMixin,
    IndexingSequenceMixin[T_doc],
    IOMixinArray,
    AnyDocArray[T_doc],
):
    """
     DocArray is a container of Documents.

    A DocArray is a list of Documents of any schema. However, many
    DocArray features are only available if these Documents are
    homogeneous and follow the same schema. To precise this schema you can use
    the `DocArray[MyDocument]` syntax where MyDocument is a Document class
    (i.e. schema). This creates a DocArray that can only contains Documents of
    the type 'MyDocument'.

    ---

    ```python
    from docarray import BaseDoc, DocArray
    from docarray.typing import NdArray, ImageUrl
    from typing import Optional


    class Image(BaseDoc):
        tensor: Optional[NdArray[100]]
        url: ImageUrl


    da = DocArray[Image](
        Image(url='http://url.com/foo.png') for _ in range(10)
    )  # noqa: E510
    ```

    ---


    If your DocArray is homogeneous (i.e. follows the same schema), you can access
    fields at the DocArray level (for example `da.tensor` or `da.url`).
    You can also set fields, with `da.tensor = np.random.random([10, 100])`:

        print(da.url)
        # [ImageUrl('http://url.com/foo.png', host_type='domain'), ...]
        import numpy as np

        da.tensor = np.random.random([10, 100])
        print(da.tensor)
        # [NdArray([0.11299577, 0.47206767, 0.481723  , 0.34754724, 0.15016037,
        #          0.88861321, 0.88317666, 0.93845579, 0.60486676, ... ]), ...]

    You can index into a DocArray like a numpy array or torch tensor:


        da[0]  # index by position
        da[0:5:2]  # index by slice
        da[[0, 2, 3]]  # index by list of indices
        da[True, False, True, True, ...]  # index by boolean mask

    You can delete items from a DocArray like a Python List

        del da[0]  # remove first element from DocArray
        del da[0:5]  # remove elements for 0 to 5 from DocArray

    :param docs: iterable of Document

    """

    data: List[T_doc] = []
    _document_type: Type[BaseDoc] = AnyDoc

    def __init__(
        self,
        data: Optional[Iterable[T_doc]] = None,
    ):
        super().__init__()
        self.data: List[T_doc] = list(self._validate_docs(data)) if data else []

    def _validate_docs(self, docs: Iterable[T_doc]) -> Iterable[T_doc]:
        """
        Validate if an Iterable of Document are compatible with this DocArray
        """
        for doc in docs:
            yield self._validate_one_doc(doc)

    def _validate_one_doc(self, doc: T_doc) -> T_doc:
        """Validate if a Document is compatible with this DocArray"""
        if isinstance(doc, Dict):
            return self._document_type(**doc)
        elif not issubclass(self._document_type, AnyDoc) and not isinstance(
            doc, self._document_type
        ):
            raise ValueError(f'{doc} is not a {self._document_type}')
        return doc

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)

    def __bytes__(self) -> bytes:
        with io.BytesIO() as bf:
            self._write_bytes(bf=bf)
            return bf.getvalue()

    def append(self, doc: T_doc):
        """
        Append a Document to the DocArray. The Document must be from the same class
        as the document_type of this DocArray otherwise it will fail.
        :param doc: A Document
        """
        self.data.append(self._validate_one_doc(doc))

    def extend(self, docs: Iterable[T_doc]):
        """
        Extend a DocArray with an Iterable of Document. The Documents must be from
        the same class as the document_type of this DocArray otherwise it will
        fail.
        :param docs: Iterable of Documents
        """
        self.data.extend(self._validate_docs(docs))

    def insert(self, i: int, doc: T_doc):
        """
        Insert a Document to the DocArray. The Document must be from the same
        class as the document_type of this DocArray otherwise it will fail.
        :param i: index to insert
        :param doc: A Document
        """
        self.data.insert(i, self._validate_one_doc(doc))

    pop = _delegate_meth_to_data('pop')
    remove = _delegate_meth_to_data('remove')
    reverse = _delegate_meth_to_data('reverse')
    sort = _delegate_meth_to_data('sort')

    def _get_data_column(
        self: T,
        field: str,
    ) -> Union[MutableSequence, T, 'TorchTensor', 'NdArray']:
        """Return all values of the fields from all docs this array contains

        :param field: name of the fields to extract
        :return: Returns a list of the field value for each document
        in the array like container
        """
        field_type = self.__class__.document_type._get_field_type(field)

        if (
            not is_union_type(field_type)
            and isinstance(field_type, type)
            and issubclass(field_type, BaseDoc)
        ):
            # calling __class_getitem__ ourselves is a hack otherwise mypy complain
            # most likely a bug in mypy though
            # bug reported here https://github.com/python/mypy/issues/14111
            return DocArray.__class_getitem__(field_type)(
                (getattr(doc, field) for doc in self),
            )
        else:
            return [getattr(doc, field) for doc in self]

    def _set_data_column(
        self: T,
        field: str,
        values: Union[List, T, 'AbstractTensor'],
    ):
        """Set all Documents in this DocArray using the passed values

        :param field: name of the fields to set
        :values: the values to set at the DocArray level
        """
        ...

        for doc, value in zip(self, values):
            setattr(doc, field, value)

    def stack(
        self,
        tensor_type: Type['AbstractTensor'] = NdArray,
    ) -> 'DocArrayStacked':
        """
        Convert the DocArray into a DocArrayStacked. `Self` cannot be used
        afterwards
        :param tensor_type: Tensor Class used to wrap the stacked tensors. This is useful
        if the BaseDoc has some undefined tensor type like AnyTensor or Union of NdArray and TorchTensor
        :return: A DocArrayStacked of the same document type as self
        """
        from docarray.array.stacked.array_stacked import DocArrayStacked

        return DocArrayStacked.__class_getitem__(self.document_type)(
            self, tensor_type=tensor_type
        )

    # @classmethod
    # def validate(cls, value: Any) -> 'DocArray[T_doc]':
    #     from docarray.array.stacked.array_stacked import DocArrayStacked
    #
    #     if isinstance(value, (cls, DocArrayStacked)):
    #         return value
    #     elif isinstance(value, Dict):
    #         return cls([cls._document_type(**v) for v in value['data']])
    #     elif isinstance(value, Iterable):
    #         return cls(value)
    #     else:
    #         raise TypeError(f'Expecting an Iterable of {cls._document_type}')

    def traverse_flat(
        self: 'DocArray',
        access_path: str,
    ) -> List[Any]:
        nodes = list(AnyDocArray._traverse(node=self, access_path=access_path))
        flattened = AnyDocArray._flatten_one_level(nodes)

        return flattened

    @classmethod
    def from_protobuf(cls: Type[T], pb_msg: 'DocumentArrayProto') -> T:
        """create a Document from a protobuf message
        :param pb_msg: The protobuf message from where to construct the DocArray
        """
        return super().from_protobuf(pb_msg)

    @overload
    def __getitem__(self, item: int) -> T_doc:
        ...

    @overload
    def __getitem__(self: T, item: IndexIterType) -> T:
        ...

    def __getitem__(self, item):
        return super().__getitem__(item)
