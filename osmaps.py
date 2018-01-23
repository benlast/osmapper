#! /usr/bin/env python3.5
from collections import namedtuple
from http import HTTPStatus
import logging
import math
import os
import os.path
import sys
import urllib.parse

from convertbng.util import (
    convert_bng,
    convert_lonlat
)
import flask
import requests
import requests.exceptions

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
LOGGER.addHandler(logging.StreamHandler(sys.stdout))

OS_URL = os.getenv('OS_URL')
OS_KEY = os.getenv('OS_KEY')

if not (OS_KEY and OS_URL):
    raise RuntimeError(
        'Both OS_KEY and OS_URL must be defined'
    )


BASE_ORIGIN = os.getenv('BASE_URL', 'http://localhost:5000')
OPENSPACE_URL_BASE = 'http://openspace.ordnancesurvey.co.uk/osmapapi/ts'
OPENSPACE_URL_PARSED = urllib.parse.urlparse(OPENSPACE_URL_BASE)
OSMAPS_PATH = '/osmaps'
PROXY_PATH = '/osproxy'

SCRIPT_DIR = os.path.abspath(
    os.path.dirname(
        __file__
    )
)

# Use a single shared requests.Session (for connection pooling) - see proxy()
SESSION = requests.Session()

LayerParam = namedtuple('LayerParam', 'scale w h layers'.split())


LAYER_PARAMS = [
    LayerParam(scale=250, w=250, h=250, layers=1),
    LayerParam(scale=1000, w=200, h=200, layers=5),
    LayerParam(scale=5000, w=200, h=200, layers=25),
    LayerParam(scale=20000, w=200, h=200, layers=100)
]

SIZES = frozenset(
    layer.w
    for layer in LAYER_PARAMS
)


PNGS = {
    name: {
        size: open(
            os.path.join(
                SCRIPT_DIR,
                '{}_{}x{}.png'.format(name, size, size)
            ), 'rb'
        ).read()
        for size in SIZES
    }
    for name in ['transparent', 'error', '404']
}


def lat_lon_to_north_east(lat, lon):
    """
    The Geo::Coordinates::OSGB description of ll_to_grid, which was used in the
    original implementation of this function:

        ll_to_grid translates a latitude and longitude pair into a grid easting
        and northing pair.

        The arguments should be supplied as real numbers representing decimal degrees.

        Following the normal mathematical convention, positive arguments mean North or East,
        negative South or West.

    Assumes that the lat/long are WGS84.

    :param lat: latitude in decimal degrees
    :param lon: longitude in decimal degrees
    :return: (n, e) grid easting and northing pair as a 2-tuple
    """
    # Convert from lat/lon into OSGB
    easts, norths = convert_bng(lon, lat)
    return norths[0], easts[0]


def north_east_to_lat_lon(north, east):
    """
    Convert nor/east to lat/long,
    then return lat/long in WGS84
    """
    lons, lats = convert_lonlat([east], [north])
    return lats[0], lons[0]


def convergence(lat, long):
    return (long + 2.0) * math.sin(lat * math.pi / 180.0)


def make_url(e_from, n_from, e_to, n_to, layer_index):
    """
    Build an OpenSpace URL from the parameters
    """
    layer_params = LAYER_PARAMS[layer_index]
    # Within the href of an Icon in a KML file, an ampersand must be encoded for the KML to be valid XML.
    # See https://stackoverflow.com/questions/10582547/percent-encoded-urls-are-mangled-by-google-earth
    return '&amp;'.join(
        [
            '{base}{path}?FORMAT=image%2Fpng',
            'SERVICE=WMS',
            'VERSION=1.1.1',
            'REQUEST=GetMap',
            'STYLES=raster',
            'EXCEPTIONS=application%2Fvnd.ogc.se_inimage',
            'LAYERS={layer}',
            'SRS=EPSG%3A27700',
            'BBOX={e_from},{n_from},{e_to},{n_to}',
            'WIDTH={w}',
            'HEIGHT={h}'
        ]
    ).format(
        base=BASE_ORIGIN,
        path=PROXY_PATH,
        e_from=e_from,
        n_from=n_from,
        e_to=e_to,
        n_to=n_to,
        w=layer_params.w,
        h=layer_params.h,
        layer=layer_params.layers
    )


KML_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://earth.google.com/kml/2.2">
<Folder>
    <name>Unofficial OS Overlay V2</name>
    <ScreenOverlay>
        <name><![CDATA[© Crown Copyright and database right 2008. All rights reserved. <a href="http://openspace.ordnancesurvey.co.uk/openspace/developeragreement.html#enduserlicense">End User License Agreement</a>]]></name>
        <Icon>https://www.ordnancesurvey.co.uk/images/ui/os-logo.png</Icon>
        <screenXY x="105" y="45" xunits="pixels" yunits="pixels" />
        <color>80ffffff</color>
    </ScreenOverlay>"""


def kml_overlay(url, s, w, n, e, t, b, r, l, rot):
    return """\
    <GroundOverlay>
        <name>OS Overlay</name>
        <description>{s},{w},{n},{e}</description>
        <Icon>
            <href>{url}</href>
        </Icon>
        <LatLonBox>
            <north>{t}</north>
            <south>{b}</south>
            <east>{r}</east>
            <west>{l}</west>
            <rotation>{rot}</rotation>
        </LatLonBox>
    </GroundOverlay>""".format(s=s, w=w, n=n, e=e, url=url, t=t, b=b, r=r, l=l, rot=rot)


KML_FOOTER = """\
    </Folder>
</kml>"""


def bad_request(message):
    LOGGER.error('Bad request: {}'.format(message))
    response = flask.Response(
        status=message,
        response=message,
        headers={
            'Content-Type': 'text/plain'
        }
    )
    response.status_code = HTTPStatus.BAD_REQUEST
    return response


MINUS_5_TO_PLUS_5 = list(range(-5, 6))


def osmaps():
    # Start the response body
    body = [KML_HEADER]

    # Decode the BBOX query string
    # ?BBOX = 54.39842700542721, -2.036022996438413, 5744.95
    # lat,long,range = (env->{QUERY_STRING} =~ / BBOX=([-\d.]+), ([-\d.]+), ([-\d.]+) /);
    bbox = flask.request.args.get('BBOX')
    if not bbox:
        return bad_request(
            'Missing or empty BBOX query parameter'
        )

    try:
        # rnge is the 'range' - renamed to avoid shadowing the builtin range() function
        lat, lon, rnge = [
            float(arg.strip())
            for arg in bbox.split(',')
        ]

    except ValueError as ex:
        return bad_request(
            'Error converting BBOX parameters to three floating point values ({})'.format(ex)
        )

    if (lon < -9.0) or (lat < 49.0) or (lon > 3.0) or (lat > 62.0) or (rnge > 1000000.0):
        # "Way off"
        # Parameters are out of range, so we just return an empty KML
        LOGGER.error(
            'Parameters out of range: lon={}, lat={}, range={}'.format(lon, lat, rnge)
        )

    else:
        # Convert the lat/long into nor/east
        north, east = lat_lon_to_north_east(lat, lon)

        # todo - can we make this test work?
        if not (north and east):
            return bad_request(
                'No origin'
            )

        # Choose appropriate TYPE index depending on BBOX range
        if rnge > 45000:
            layer_index = 3
        elif rnge > 10000:
            layer_index = 2
        elif rnge > 2000:
            layer_index = 1
        else:
            layer_index = 0

        unit = LAYER_PARAMS[layer_index].scale

        # Python % doesn't coerce float to int
        orig_s = north - (north % unit)
        orig_w = east - (east % unit)

        for i in MINUS_5_TO_PLUS_5:
            s = orig_s + (unit * i)

            for j in MINUS_5_TO_PLUS_5:
                w = orig_w + (unit * j)
                n = s + unit
                e = w + unit

                url = make_url(w, s, e, n, layer_index)

                b = (north_east_to_lat_lon(s, w + (0.5 * unit)))[0]
                t = (north_east_to_lat_lon(n, w + (0.5 * unit)))[0]
                r = (north_east_to_lat_lon(s + (0.5 * unit), e))[1]
                l = (north_east_to_lat_lon(s + (0.5 * unit), w))[1]

                c_lat, c_lon = north_east_to_lat_lon(s + (0.5 * unit), w + (0.5 * unit))

                rot = -1 * convergence(c_lat, c_lon)

                body.append(
                    kml_overlay(url, s, w, n, e, t, b, r, l, rot)
                )

    body.append(KML_FOOTER)

    return flask.make_response(
        (
            '\n'.join(body),
            HTTPStatus.OK,
            {
                'Content-Type': 'application/vnd.google-earth.kml+xml'
            }
        )

    )


def proxy():
    """
    Proxy a request through to openspace
    """
    headers = dict(
        (
            (key.lower(), value)
            for key, value in flask.request.headers.items()
        )
    )

    headers['user-agent'] = (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_2) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/63.0.3239.132 Safari/537.36'
    )

    headers['host'] = OPENSPACE_URL_PARSED.netloc

    headers['origin'] = '{}://{}'.format(
        OPENSPACE_URL_PARSED.scheme,
        OPENSPACE_URL_PARSED.netloc
    )

    headers['referer'] = OS_URL

    # Clone the request args so we have a mutable copy
    args = flask.request.args.copy()

    image_size = int(args.get('WIDTH') or args.get('width') or 200)

    # Add in the OS params
    args['KEY'] = OS_KEY
    args['URL'] = OS_URL

    os_query = urllib.parse.urlencode(
        args,
        doseq=False,
        quote_via=lambda str, _, enc, err: urllib.parse.quote(str, safe=',', encoding=enc, errors=err)
    )

    os_url = urllib.parse.urlunparse(
        (
            OPENSPACE_URL_PARSED.scheme,
            OPENSPACE_URL_PARSED.netloc,
            OPENSPACE_URL_PARSED.path,
            '',     # params - not used
            os_query,
            ''      # fragment - not used
        )
    )

    for retry in (True, True, False):
        try:
            os_response = SESSION.get(
                os_url,
                headers=headers
            )

            # Exit the loop if the status code is a non-retryable one
            if os_response.status_code in (
                HTTPStatus.OK,
                HTTPStatus.NOT_FOUND,
                HTTPStatus.INTERNAL_SERVER_ERROR
            ):
                break

            # If the status code is retryable, loop
            LOGGER.warning(
                'Retrying after response with status code {}'.format(os_response.status_code)
            )

        except requests.exceptions.RequestException as ex:
            # Retryable exceptions
            if isinstance(
                    ex,
                    requests.exceptions.ReadTimeout
            ) and retry:
                LOGGER.warning(
                    'Retrying after exception {}'.format(ex)
                )
                continue

            LOGGER.error(
                'Exception requesting {}: {}'.format(
                    os_url,
                    ex
                )
            )
            content = PNGS['error'][image_size]
            return flask.Response(
                response=content,
                headers={
                    'Content-Type': 'image/png',
                    'Content-Length': len(content)
                }
            )

    # Convert the requests.Response headers dict to one
    # usable by flask.Response
    os_headers = {
        key.lower(): value
        for key, value in os_response.headers.items()
    }

    if os_response.status_code == HTTPStatus.OK:
        os_headers.pop('expires')
        os_headers['cache-control'] = 'max-age=120'
        response = flask.Response(
            response=os_response.content,
            headers=os_headers
        )

    else:
        LOGGER.warning(
            'Swallowing HTTP status {} from OS'.format(
                os_response.status_code
            )
        )
        content = PNGS[
            '404' if os_response.status_code == HTTPStatus.NOT_FOUND else 'transparent'
        ][image_size]
        response = flask.Response(
            response=content,
            headers={
                'Content-Type': 'image/png',
                'Content-Length': len(content)
            }
        )

    response.status_code = HTTPStatus.OK

    return response


APP = flask.Flask(__name__)
APP.add_url_rule(OSMAPS_PATH, 'osmaps', osmaps)
APP.add_url_rule(PROXY_PATH, 'osproxy', proxy)


if __name__ == '__main__':
    APP.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
