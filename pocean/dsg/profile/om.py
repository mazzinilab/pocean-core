#!python
# coding=utf-8
import six
from copy import copy
from collections import OrderedDict

import numpy as np
import pandas as pd

from pocean.utils import (
    generic_masked,
    get_default_axes,
    get_mapped_axes_variables,
    get_masked_datetime_array,
    normalize_array,
    normalize_countable_array,
)
from pocean.cf import CFDataset
from pocean.dsg.profile import profile_calculated_metadata

from pocean import logger as L  # noqa


class OrthogonalMultidimensionalProfile(CFDataset):
    """
    If the profile instances have the same number of elements and the vertical
    coordinate values are identical for all instances, you may use the
    orthogonal multidimensional array representation. This has either a
    one-dimensional coordinate variable, z(z), provided the vertical coordinate
    values are ordered monotonically, or a one-dimensional auxiliary coordinate
    variable, alt(o), where o is the element dimension. In the former case,
    listing the vertical coordinate variable in the coordinates attributes of
    the data variables is optional.
    """

    @classmethod
    def is_mine(cls, dsg):
        try:
            pvars = dsg.filter_by_attrs(cf_role='profile_id')
            assert len(pvars) == 1
            assert dsg.featureType.lower() == 'profile'
            assert len(dsg.t_axes()) == 1
            assert len(dsg.x_axes()) == 1
            assert len(dsg.y_axes()) == 1
            assert len(dsg.z_axes()) == 1

            # Allow for string variables
            pvar = pvars[0]
            # 0 = single
            # 1 = array of strings/ints/bytes/etc
            # 2 = array of character arrays
            assert 0 <= len(pvar.dimensions) <= 2

            ps = normalize_array(pvar)
            is_single = isinstance(ps, six.string_types) or ps.size == 1

            t = dsg.t_axes()[0]
            x = dsg.x_axes()[0]
            y = dsg.y_axes()[0]
            z = dsg.z_axes()[0]
            assert len(z.dimensions) == 1
            z_dim = dsg.dimensions[z.dimensions[0]]

            if is_single:
                assert t.size == 1
                assert x.size == 1
                assert y.size == 1
                for dv in dsg.data_vars():
                    assert len(dv.dimensions) == 1
                    assert z_dim.name in dv.dimensions
                    assert dv.size == z_dim.size
            else:
                assert t.size == pvar.size
                assert x.size == pvar.size
                assert y.size == pvar.size
                p_dim = dsg.dimensions[pvar.dimensions[0]]
                for dv in dsg.data_vars():
                    assert len(dv.dimensions) == 2
                    assert z_dim.name in dv.dimensions
                    assert p_dim.name in dv.dimensions
                    assert dv.size == z_dim.size * p_dim.size

        except BaseException:
            return False

        return True

    @classmethod
    def from_dataframe(cls, df, output, **kwargs):
        raise NotImplementedError

    def calculated_metadata(self, df=None, geometries=True, clean_cols=True, clean_rows=True, **kwargs):
        axes = get_default_axes(kwargs.pop('axes', {}))
        if df is None:
            df = self.to_dataframe(clean_cols=clean_cols, clean_rows=clean_rows, axes=axes)
        return profile_calculated_metadata(df, axes, geometries)

    def to_dataframe(self, clean_cols=True, clean_rows=True, **kwargs):
        axes = get_default_axes(kwargs.pop('axes', {}))

        axv = get_mapped_axes_variables(self, axes)

        zvar = axv.z
        zs = len(self.dimensions[zvar.dimensions[0]])

        # Profiles
        pvar = axv.profile
        p = normalize_countable_array(pvar)
        ps = p.size
        p = p.repeat(zs)

        # Z
        z = generic_masked(axv.z[:], attrs=self.vatts(axv.z.name))
        try:
            z = np.tile(z, ps)
        except ValueError:
            z = z.flatten()

        # T
        tvar = axv.t
        t = tvar[:].repeat(zs)
        nt = get_masked_datetime_array(t, tvar).flatten()

        # X
        xvar = axv.x
        x = generic_masked(xvar[:].repeat(zs), attrs=self.vatts(xvar.name))

        # Y
        yvar = axv.y
        y = generic_masked(yvar[:].repeat(zs), attrs=self.vatts(yvar.name))

        df_data = OrderedDict([
            (axes.t, nt),
            (axes.x, x),
            (axes.y, y),
            (axes.z, z),
            (axes.profile, p)
        ])

        building_index_to_drop = np.ones(t.size, dtype=bool)

        # Axes variables are already processed so skip them
        extract_vars = copy(self.variables)
        for ncvar in axv._asdict().values():
            if ncvar is not None and ncvar.name in extract_vars:
                del extract_vars[ncvar.name]

        for i, (dnam, dvar) in enumerate(extract_vars.items()):

            # Profile dimension
            if dvar.dimensions == pvar.dimensions:
                vdata = generic_masked(dvar[:].repeat(zs).astype(dvar.dtype), attrs=self.vatts(dnam))
                building_index_to_drop = (building_index_to_drop == True) & (vdata.mask == True)  # noqa

            # Z dimension
            elif dvar.dimensions == zvar.dimensions:
                vdata = generic_masked(np.tile(dvar[:], ps).flatten().astype(dvar.dtype), attrs=self.vatts(dnam))
                building_index_to_drop = (building_index_to_drop == True) & (vdata.mask == True)  # noqa

            # Profile, z dimension
            elif dvar.dimensions == pvar.dimensions + zvar.dimensions:
                vdata = generic_masked(dvar[:].flatten().astype(dvar.dtype), attrs=self.vatts(dnam))
                building_index_to_drop = (building_index_to_drop == True) & (vdata.mask == True)  # noqa

            else:
                vdata = generic_masked(dvar[:].flatten().astype(dvar.dtype), attrs=self.vatts(dnam))
                # Carry through size 1 variables
                if vdata.size == 1:
                    if vdata[0] is np.ma.masked:
                        L.warning("Skipping variable {} that is completely masked".format(dnam))
                        continue
                    vdata = vdata[0]
                else:
                    L.warning("Skipping variable {} since it didn't match any dimension sizes".format(dnam))
                    continue

            df_data[dnam] = vdata

        df = pd.DataFrame(df_data)

        # Drop all data columns with no data
        if clean_cols:
            df = df.dropna(axis=1, how='all')

        # Drop all data rows with no data variable data
        if clean_rows:
            df = df.iloc[~building_index_to_drop]

        return df
