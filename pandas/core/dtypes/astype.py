"""
Functions for implementing 'astype' methods according to pandas conventions,
particularly ones that differ from numpy.
"""
from __future__ import annotations

import inspect
from typing import (
    TYPE_CHECKING,
    cast,
    overload,
)
import warnings

import numpy as np

from pandas._libs import lib
from pandas._typing import (
    ArrayLike,
    DtypeObj,
)
from pandas.errors import IntCastingNaNError
from pandas.util._exceptions import find_stack_level

from pandas.core.dtypes.common import (
    is_datetime64_dtype,
    is_datetime64tz_dtype,
    is_dtype_equal,
    is_object_dtype,
    is_timedelta64_dtype,
    pandas_dtype,
)
from pandas.core.dtypes.dtypes import (
    DatetimeTZDtype,
    ExtensionDtype,
    PandasDtype,
)
from pandas.core.dtypes.missing import isna

if TYPE_CHECKING:
    from pandas.core.arrays import (
        DatetimeArray,
        ExtensionArray,
    )


_dtype_obj = np.dtype(object)


@overload
def astype_nansafe(
    arr: np.ndarray, dtype: np.dtype, copy: bool = ..., skipna: bool = ...
) -> np.ndarray:
    ...


@overload
def astype_nansafe(
    arr: np.ndarray, dtype: ExtensionDtype, copy: bool = ..., skipna: bool = ...
) -> ExtensionArray:
    ...


def astype_nansafe(
    arr: np.ndarray, dtype: DtypeObj, copy: bool = True, skipna: bool = False
) -> ArrayLike:
    """
    Cast the elements of an array to a given dtype a nan-safe manner.

    Parameters
    ----------
    arr : ndarray
    dtype : np.dtype or ExtensionDtype
    copy : bool, default True
        If False, a view will be attempted but may fail, if
        e.g. the item sizes don't align.
    skipna: bool, default False
        Whether or not we should skip NaN when casting as a string-type.

    Raises
    ------
    ValueError
        The dtype was a datetime64/timedelta64 dtype, but it had no unit.
    """
    if arr.ndim > 1:
        flat = arr.ravel()
        result = astype_nansafe(flat, dtype, copy=copy, skipna=skipna)
        # error: Item "ExtensionArray" of "Union[ExtensionArray, ndarray]" has no
        # attribute "reshape"
        return result.reshape(arr.shape)  # type: ignore[union-attr]

    # We get here with 0-dim from sparse
    arr = np.atleast_1d(arr)

    # dispatch on extension dtype if needed
    if isinstance(dtype, ExtensionDtype):
        return dtype.construct_array_type()._from_sequence(arr, dtype=dtype, copy=copy)

    elif not isinstance(dtype, np.dtype):  # pragma: no cover
        raise ValueError("dtype must be np.dtype or ExtensionDtype")

    if arr.dtype.kind in ["m", "M"] and (
        issubclass(dtype.type, str) or dtype == _dtype_obj
    ):
        from pandas.core.construction import ensure_wrapped_if_datetimelike

        arr = ensure_wrapped_if_datetimelike(arr)
        return arr.astype(dtype, copy=copy)

    if issubclass(dtype.type, str):
        return lib.ensure_string_array(arr, skipna=skipna, convert_na_value=False)

    elif is_datetime64_dtype(arr.dtype):
        if dtype == np.int64:
            warnings.warn(
                f"casting {arr.dtype} values to int64 with .astype(...) "
                "is deprecated and will raise in a future version. "
                "Use .view(...) instead.",
                FutureWarning,
                stacklevel=find_stack_level(),
            )
            if isna(arr).any():
                raise ValueError("Cannot convert NaT values to integer")
            return arr.view(dtype)

        # allow frequency conversions
        if dtype.kind == "M":
            return arr.astype(dtype)

        raise TypeError(f"cannot astype a datetimelike from [{arr.dtype}] to [{dtype}]")

    elif is_timedelta64_dtype(arr.dtype):
        if dtype == np.int64:
            warnings.warn(
                f"casting {arr.dtype} values to int64 with .astype(...) "
                "is deprecated and will raise in a future version. "
                "Use .view(...) instead.",
                FutureWarning,
                stacklevel=find_stack_level(),
            )
            if isna(arr).any():
                raise ValueError("Cannot convert NaT values to integer")
            return arr.view(dtype)

        elif dtype.kind == "m":
            return astype_td64_unit_conversion(arr, dtype, copy=copy)

        raise TypeError(f"cannot astype a timedelta from [{arr.dtype}] to [{dtype}]")

    elif np.issubdtype(arr.dtype, np.floating) and np.issubdtype(dtype, np.integer):
        return _astype_float_to_int_nansafe(arr, dtype, copy)

    elif is_object_dtype(arr.dtype):

        # work around NumPy brokenness, #1987
        if np.issubdtype(dtype.type, np.integer):
            return lib.astype_intsafe(arr, dtype)

        # if we have a datetime/timedelta array of objects
        # then coerce to a proper dtype and recall astype_nansafe

        elif is_datetime64_dtype(dtype):
            from pandas import to_datetime

            return astype_nansafe(
                to_datetime(arr).values,
                dtype,
                copy=copy,
            )
        elif is_timedelta64_dtype(dtype):
            from pandas import to_timedelta

            return astype_nansafe(to_timedelta(arr)._values, dtype, copy=copy)

    if dtype.name in ("datetime64", "timedelta64"):
        msg = (
            f"The '{dtype.name}' dtype has no unit. Please pass in "
            f"'{dtype.name}[ns]' instead."
        )
        raise ValueError(msg)

    if copy or is_object_dtype(arr.dtype) or is_object_dtype(dtype):
        # Explicit copy, or required since NumPy can't view from / to object.
        return arr.astype(dtype, copy=True)

    return arr.astype(dtype, copy=copy)


def _astype_float_to_int_nansafe(
    values: np.ndarray, dtype: np.dtype, copy: bool
) -> np.ndarray:
    """
    astype with a check preventing converting NaN to an meaningless integer value.
    """
    if not np.isfinite(values).all():
        raise IntCastingNaNError(
            "Cannot convert non-finite values (NA or inf) to integer"
        )
    return values.astype(dtype, copy=copy)


def astype_array(values: ArrayLike, dtype: DtypeObj, copy: bool = False) -> ArrayLike:
    """
    Cast array (ndarray or ExtensionArray) to the new dtype.

    Parameters
    ----------
    values : ndarray or ExtensionArray
    dtype : dtype object
    copy : bool, default False
        copy if indicated

    Returns
    -------
    ndarray or ExtensionArray
    """
    if (
        values.dtype.kind in ["m", "M"]
        and dtype.kind in ["i", "u"]
        and isinstance(dtype, np.dtype)
        and dtype.itemsize != 8
    ):
        # TODO(2.0) remove special case once deprecation on DTA/TDA is enforced
        msg = rf"cannot astype a datetimelike from [{values.dtype}] to [{dtype}]"
        raise TypeError(msg)

    if is_datetime64tz_dtype(dtype) and is_datetime64_dtype(values.dtype):
        return astype_dt64_to_dt64tz(values, dtype, copy, via_utc=True)

    if is_dtype_equal(values.dtype, dtype):
        if copy:
            return values.copy()
        return values

    if not isinstance(values, np.ndarray):
        # i.e. ExtensionArray
        values = values.astype(dtype, copy=copy)

    else:
        values = astype_nansafe(values, dtype, copy=copy)

    # in pandas we don't store numpy str dtypes, so convert to object
    if isinstance(dtype, np.dtype) and issubclass(values.dtype.type, str):
        values = np.array(values, dtype=object)

    return values


def astype_array_safe(
    values: ArrayLike, dtype, copy: bool = False, errors: str = "raise"
) -> ArrayLike:
    """
    Cast array (ndarray or ExtensionArray) to the new dtype.

    This basically is the implementation for DataFrame/Series.astype and
    includes all custom logic for pandas (NaN-safety, converting str to object,
    not allowing )

    Parameters
    ----------
    values : ndarray or ExtensionArray
    dtype : str, dtype convertible
    copy : bool, default False
        copy if indicated
    errors : str, {'raise', 'ignore'}, default 'raise'
        - ``raise`` : allow exceptions to be raised
        - ``ignore`` : suppress exceptions. On error return original object

    Returns
    -------
    ndarray or ExtensionArray
    """
    errors_legal_values = ("raise", "ignore")

    if errors not in errors_legal_values:
        invalid_arg = (
            "Expected value of kwarg 'errors' to be one of "
            f"{list(errors_legal_values)}. Supplied value is '{errors}'"
        )
        raise ValueError(invalid_arg)

    if inspect.isclass(dtype) and issubclass(dtype, ExtensionDtype):
        msg = (
            f"Expected an instance of {dtype.__name__}, "
            "but got the class instead. Try instantiating 'dtype'."
        )
        raise TypeError(msg)

    dtype = pandas_dtype(dtype)
    if isinstance(dtype, PandasDtype):
        # Ensure we don't end up with a PandasArray
        dtype = dtype.numpy_dtype

    try:
        new_values = astype_array(values, dtype, copy=copy)
    except (ValueError, TypeError):
        # e.g. astype_nansafe can fail on object-dtype of strings
        #  trying to convert to float
        if errors == "ignore":
            new_values = values
        else:
            raise

    return new_values


def astype_td64_unit_conversion(
    values: np.ndarray, dtype: np.dtype, copy: bool
) -> np.ndarray:
    """
    By pandas convention, converting to non-nano timedelta64
    returns an int64-dtyped array with ints representing multiples
    of the desired timedelta unit.  This is essentially division.

    Parameters
    ----------
    values : np.ndarray[timedelta64[ns]]
    dtype : np.dtype
        timedelta64 with unit not-necessarily nano
    copy : bool

    Returns
    -------
    np.ndarray
    """
    if is_dtype_equal(values.dtype, dtype):
        if copy:
            return values.copy()
        return values

    # otherwise we are converting to non-nano
    result = values.astype(dtype, copy=False)  # avoid double-copying
    result = result.astype(np.float64)

    mask = isna(values)
    np.putmask(result, mask, np.nan)
    return result


def astype_dt64_to_dt64tz(
    values: ArrayLike, dtype: DtypeObj, copy: bool, via_utc: bool = False
) -> DatetimeArray:
    # GH#33401 we have inconsistent behaviors between
    #  Datetimeindex[naive].astype(tzaware)
    #  Series[dt64].astype(tzaware)
    # This collects them in one place to prevent further fragmentation.

    from pandas.core.construction import ensure_wrapped_if_datetimelike

    values = ensure_wrapped_if_datetimelike(values)
    values = cast("DatetimeArray", values)
    aware = isinstance(dtype, DatetimeTZDtype)

    if via_utc:
        # Series.astype behavior

        # caller is responsible for checking this
        assert values.tz is None and aware
        dtype = cast(DatetimeTZDtype, dtype)

        if copy:
            # this should be the only copy
            values = values.copy()

        warnings.warn(
            "Using .astype to convert from timezone-naive dtype to "
            "timezone-aware dtype is deprecated and will raise in a "
            "future version.  Use ser.dt.tz_localize instead.",
            FutureWarning,
            stacklevel=find_stack_level(),
        )

        # GH#33401 this doesn't match DatetimeArray.astype, which
        #  goes through the `not via_utc` path
        return values.tz_localize("UTC").tz_convert(dtype.tz)

    else:
        # DatetimeArray/DatetimeIndex.astype behavior
        if values.tz is None and aware:
            dtype = cast(DatetimeTZDtype, dtype)
            warnings.warn(
                "Using .astype to convert from timezone-naive dtype to "
                "timezone-aware dtype is deprecated and will raise in a "
                "future version.  Use obj.tz_localize instead.",
                FutureWarning,
                stacklevel=find_stack_level(),
            )

            return values.tz_localize(dtype.tz)

        elif aware:
            # GH#18951: datetime64_tz dtype but not equal means different tz
            dtype = cast(DatetimeTZDtype, dtype)
            result = values.tz_convert(dtype.tz)
            if copy:
                result = result.copy()
            return result

        elif values.tz is not None:
            warnings.warn(
                "Using .astype to convert from timezone-aware dtype to "
                "timezone-naive dtype is deprecated and will raise in a "
                "future version.  Use obj.tz_localize(None) or "
                "obj.tz_convert('UTC').tz_localize(None) instead",
                FutureWarning,
                stacklevel=find_stack_level(),
            )

            result = values.tz_convert("UTC").tz_localize(None)
            if copy:
                result = result.copy()
            return result

        raise NotImplementedError("dtype_equal case should be handled elsewhere")
