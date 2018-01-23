import http
from unittest import TestCase

import osmaps


class ProjectionTests(TestCase):

    def test_ll_to_grid(self):
        for lat, lon, east, north in [
            (
                51.44533267,
                -0.32824866,
                516274.46,
                173141.101
            ),
            (
                54.589097162646141,
                -2.0183041005533306,
                398915.554,
                521544.09
            )
        ]:
            res_north, res_east = osmaps.lat_lon_to_north_east(lat, lon)
            self.assertEqual(
                (res_east, res_north),
                (east, north)
            )

    def test_grid_to_ll(self):
        for east, north, lat, lon in [
            (
                516276,
                173141,
                51.44533145,
                -0.32822654
            ),
            (
                398915,
                521545,
                54.58910534,
                -2.01831267
            )
        ]:
            res_lat, res_lon = osmaps.north_east_to_lat_lon(north, east)
            self.assertEqual(
                (res_lat, res_lon),
                (lat, lon)
            )

    def test_kml_request(self):
        client = osmaps.APP.test_client()
        response = client.get(
            '/osmaps?BBOX=54.39842700542721,%20-2.036022996438413,5744.95'
        )

        self.assertEqual(
            response.status_code,
            http.HTTPStatus.OK
        )
        self.assertEqual(
            response.content_type,
            'application/vnd.google-earth.kml+xml'
        )
