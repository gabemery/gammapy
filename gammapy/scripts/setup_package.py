# Licensed under a 3-clause BSD style license - see LICENSE.rst


def get_package_data():
    files = [
        'data_browser/templates/*',
        'data_browser/static/*',
        '*.yaml',
    ]
    return {'gammapy.scripts': files}
