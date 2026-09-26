pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'dice'
gnome = import('gnome')

gnome.compile_resources('dice',
  'dice.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'dice.in',
  output: 'dice',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x'
)

dice_sources = [
  '__init__.py',
  'main.py',
  'window.py',
  'library.py',
  'romscan.py',
  'platforms.py',
  'emulators.py',
  'covers.py',
  'models.py',
  'widgets.py',
]

install_data(dice_sources, install_dir: moduledir)
