#!python
# coding=utf-8
from copy import copy
from pocean.cf import CFDataset
from datetime import datetime
import numpy as np
import pandas as pd

from pocean.utils import (
    unique_justseen,
    normalize_array,
    get_dtype,
    dict_update,
    generic_masked
)
from pocean.cf import cf_safe_name

import netCDF4 as nc4
from pocean import logger


class OrthogonalMultidimensionalTimeseries(CFDataset):
    """
    H.2.1. Orthogonal multidimensional array representation of time series

    If the time series instances have the same number of elements and the time values are identical for all instances, you may use the orthogonal multidimensional array representation. This has either a one-dimensional coordinate variable, time(time), provided the time values are ordered monotonically, or a one-dimensional auxiliary coordinate variable, time(o), where o is the element dimension. In the former case, listing the time variable in the coordinates attributes of the data variables is optional.
    """

    @classmethod
    def is_mine(cls, dsg):
        try:
            rvars = dsg.filter_by_attrs(cf_role='timeseries_id')
            assert len(rvars) == 1
            assert dsg.featureType.lower() == 'timeseries'
            assert len(dsg.t_axes()) >= 1
            assert len(dsg.x_axes()) >= 1
            assert len(dsg.y_axes()) >= 1

            # Not a CR
            assert not dsg.filter_by_attrs(
                sample_dimension=lambda x: x is not None
            )

            # Not an IR
            assert not dsg.filter_by_attrs(
                instance_dimension=lambda x: x is not None
            )

            # OM files will always have a time variable with one dimension.
            assert len(dsg.t_axes()[0].dimensions) == 1

            # Allow for string variables
            rvar = rvars[0]
            # 0 = single
            # 1 = array of strings/ints/bytes/etc
            # 2 = array of character arrays
            assert 0 <= len(rvar.dimensions) <= 2

        except AssertionError:
            return False

        return True

    @classmethod
    def from_dataframe(cls, df, output, **kwargs):
        reserved_columns = ['station', 't', 'x', 'y', 'z']
        data_columns = [ d for d in df.columns if d not in reserved_columns ]

        with OrthogonalMultidimensionalTimeseries(output, 'w') as nc:

            station_group = df.groupby('station')
            num_stations = len(station_group)

            # Metadata variables
            nc.createVariable('crs', 'i4')

            # Create all of the variables
            nc.createDimension('time', df.t.size)
            nc.createDimension('station', num_stations)
            station = nc.createVariable('station', get_dtype(df.station))

            time = nc.createVariable('time', 'f8', ('time',))
            latitude = nc.createVariable('latitude', get_dtype(df.y), ('station',))
            longitude = nc.createVariable('longitude', get_dtype(df.x), ('station',))
            z = nc.createVariable('z', get_dtype(df.z), ('station',), fill_value=df.z.dtype.type(cls.default_fill_value))

            attributes = dict_update(nc.nc_attributes(), kwargs.pop('attributes', {}))

            logger.info(df.t.values.dtype)
            time[:] = nc4.date2num(df.t.tolist(), units=cls.default_time_unit)

            for i, (uid, sdf) in enumerate(station_group):
                station[i] = uid
                latitude[i] = sdf.y.iloc[0]
                longitude[i] = sdf.x.iloc[0]

                # TODO: write a test for a Z with a _FillValue
                z[i] = sdf.z.iloc[0]

                for c in data_columns:

                    # Create variable if it doesn't exist
                    var_name = cf_safe_name(c)
                    if var_name not in nc.variables:
                        if np.issubdtype(sdf[c].dtype, 'S') or sdf[c].dtype == object:
                            # AttributeError: cannot set _FillValue attribute for VLEN or compound variable
                            v = nc.createVariable(var_name, get_dtype(sdf[c]), ('station', 'time',))
                        else:
                            v = nc.createVariable(var_name, get_dtype(sdf[c]), ('station', 'time',), fill_value=sdf[c].dtype.type(cls.default_fill_value))

                        if var_name not in attributes:
                            attributes[var_name] = {}
                        attributes[var_name] = dict_update(attributes[var_name], {
                            'coordinates' : 'time latitude longitude z',
                        })
                    else:
                        v = nc.variables[var_name]

                    if hasattr(v, '_FillValue'):
                        vvalues = sdf[c].fillna(v._FillValue).values
                    else:
                        # Use an empty string... better than nothing!
                        vvalues = sdf[c].fillna('').values

                    try:
                        v[i, :] = vvalues
                    except BaseException:
                        logger.error('{} NOPE'.format(v.name))

            # Set global attributes
            nc.update_attributes(attributes)

        return OrthogonalMultidimensionalTimeseries(output, **kwargs)


    def calculated_metadata(self, df=None, geometries=True, clean_cols=True, clean_rows=True):
        # if df is None:
        #     df = self.to_dataframe(clean_cols=clean_cols, clean_rows=clean_rows)
        raise NotImplementedError

    def to_dataframe(self, clean_cols=False, clean_rows=False):

        # Don't pass around the attributes store them in the class

        svar = self.filter_by_attrs(cf_role='timeseries_id')[0]
        # Stations
        # TODO: Make sure there is a test for a file with multiple time variables
        try:
            s = normalize_array(svar)
        except ValueError:
            s = np.asarray(list(range(len(svar))), dtype=np.integer)
        logger.debug(['station data size: ', s.size])

        # T
        tvar = self.t_axes()[0]
        t = nc4.num2date(tvar[:], tvar.units, getattr(tvar, 'calendar', 'standard'))
        if isinstance(t, datetime):
            # Size one
            t = np.array([t.isoformat()], dtype='datetime64')
        logger.debug(['time data size: ', t.size])

        # X
        xvar = self.x_axes()[0]
        x = generic_masked(xvar[:].repeat(t.size), attrs=self.vatts(xvar.name)).round(5)
        logger.debug(['x data size: ', x.size])

        # Y
        yvar = self.y_axes()[0]
        y = generic_masked(yvar[:].repeat(t.size), attrs=self.vatts(yvar.name)).round(5)
        logger.debug(['y data size: ', y.size])

        # Z
        zvar = self.z_axes()[0]
        z = generic_masked(zvar[:].repeat(t.size), attrs=self.vatts(zvar.name))
        logger.debug(['z data size: ', z.size])

        df_data = {
            't': t,
            'x': x,
            'y': y,
            'z': z,
            'station': s,
        }

        #building_index_to_drop = np.ones(t.size, dtype=bool)
        extract_vars = copy(self.variables)
        del extract_vars[svar.name]
        del extract_vars[xvar.name]
        del extract_vars[yvar.name]
        del extract_vars[zvar.name]
        del extract_vars[tvar.name]

        for i, (dnam, dvar) in enumerate(extract_vars.items()):
            if dvar[:].flatten().size > t.size:
                logger.warning("Variable {} is not the correct size, skipping.".format(dnam))
                continue

            vdata = generic_masked(dvar[:].flatten(), attrs=self.vatts(dnam))
            if vdata.size == 1:
                vdata = vdata[0]
            #building_index_to_drop = (building_index_to_drop == True) & (vdata.mask == True)  # noqa
            df_data[dnam] = vdata
            #logger.info('{} - {}'.format(dnam, vdata.shape))

        df = pd.DataFrame()
        for k, v in df_data.items():
            df[k] = v

        # Drop all data columns with no data
        if clean_cols:
            df = df.dropna(axis=1, how='all')

        # Drop all data rows with no data variable data
        #if clean_rows:
        #    df = df.iloc[~building_index_to_drop]

        return df