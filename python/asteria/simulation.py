# -*- coding: utf-8 -*-
"""CCSN neutrino sources.

This module encapsulates the basic parameters of neutrino fluxes from
supernovae as modeled in the CCSN literature. For each species of neutrino one
requires an estimate of the luminosity vs. time as well as the energy spectrum
of the neutrinos at any given time.
"""

from __future__ import print_function, division

from astropy import units as u
from snewpy.neutrino import Flavor, MassHierarchy
from snewpy import flavor_transformation as ft

import numpy as np
import configparser
import os

from .interactions import Interactions
from .source import Source
from .detector import Detector


class Simulation:
    """ Top-level class for performing ASTERIA's core simulation routine, and handler for the resulting outputs
    """
    def __init__(self, config=None, *, model=None, distance=10 * u.kpc, flavors=None, hierarchy=None,
                 interactions=Interactions, mixing_scheme=None, mixing_angle=None, E=None, Emin=None, Emax=None,
                 dE=None, t=None, tmin=None, tmax=None, dt=None, geomfile=None, effvolfile=None):
        self.param = {}
        if model and not config:

            if not E and None in (Emin, Emax, dE):
                raise ValueError("Missing or incomplete energy range definition. Use argument `E` or "
                                 "arguments `Emin`, `Emax`, `dE")
            elif not E and None not in (Emin, Emax, dE):
                _Emin = Emin.to(u.MeV).value
                _Emax = Emax.to(u.MeV).value
                _dE = dE.to(u.MeV).value
                E = np.arange(_Emin, _Emax + _dE, _dE) * u.MeV
            elif not E:
                E = np.arange(0, 100, 1) * u.MeV

            if not t and None in (tmin, tmax, dt):
                raise ValueError("Missing or incomplete energy range definition. Use argument `t` or "
                                 "arguments `tmin`, `tmax`, `dt")
            elif not t and None not in (tmin, tmax, dt):
                _tmin = tmin.to(u.ms).value
                _tmax = tmax.to(u.ms).value
                _dt = dt.to(u.ms)
                t = np.arange(_tmin, _tmax + _dt.value, _dt.value) * u.ms
                t = t.to(u.s)
            elif t:
                _dt = np.ediff1d(t)[0]
            else:
                t = np.arange(-1, 1, 0.001) * u.s
                _dt = 1 * u.ms

            self.source = Source(model['name'], model['param'])
            self.distance = distance
            self.energy = E
            self.time = t
            self._sim_dt = _dt
            self._res_dt = 2 * u.ms  # TODO: Add config/arg option for this
            if flavors is None:
                self.flavors = Flavor
            else:
                self.flavors = flavors

            if not hierarchy or hierarchy.upper() == 'DEFAULT':
                self.hierarchy = MassHierarchy.NORMAL
            else:
                self.hierarchy = getattr(MassHierarchy, hierarchy.upper())

            self.mixing_scheme = mixing_scheme
            self.mixing_angle = mixing_angle
            if mixing_scheme:
                if mixing_scheme == 'NoTransformation':
                    self._mixing = getattr(ft, mixing_scheme)()
                else:
                    # TODO: Improve mixing name checking, this argument is case sensitive
                    self._mixing = getattr(ft, mixing_scheme)(mh=self.hierarchy)
            else:
                self._mixing = ft.NoTransformation()

            self.interactions = interactions
            self._E_per_V = None
            self._total_E_per_V = None
            self._photon_spectra = None
            self._create_paramdict(model, distance, flavors, hierarchy, interactions, mixing_scheme, mixing_angle, E, t)

            if not geomfile:
                self._geomfile = os.path.join(os.environ['ASTERIA'],
                                              'data/detector/Icecube_geometry.20110102.complete.txt')
            else:
                self._geomfile = geomfile

            if not effvolfile:
                self._effvolfile = os.path.join(os.environ['ASTERIA'],
                                                'data/detector/effectivevolume_benedikt_AHA_normalDoms.txt')
            else:
                self._effvolfile = effvolfile

            self.detector = Detector(self._geomfile, self._effvolfile)
            self._eps_i3 = None
            self._eps_dc = None
            self._time_binned = None
            self._E_per_V_binned = None
            self._total_E_per_V_binned = None
            self._result_ready = False

        elif config is not None:
            with open(config) as f:
                configuration = configparser.ConfigParser()
                configuration.read_file(f)
                basic = configuration['BASIC']
                model = configuration['MODEL']
                mixing = configuration['MIXING']
                energy = configuration['ENERGY']
                time = configuration['TIME']

                if 'min' and 'max' and 'step' in configuration['ENERGY'].keys():
                    _Emin = float(energy['min'])
                    _Emax = float(energy['max'])
                    _dE = float(energy['step'])
                    energy = np.arange(_Emin, _Emax + _dE, _dE) * u.MeV
                else:
                    energy = np.arange(0, 100, 1) * u.MeV

                if 'min' and 'max' and 'step' in configuration['TIME'].keys():
                    _tmin = float(time['min'])
                    _tmax = float(time['max'])
                    _dt = float(time['step'])
                    f = u.s.to(u.ms)
                    time = np.arange(f * _tmin, f * _tmax + _dt, _dt) * u.ms
                else:
                    time = np.arange(-1000, 1000, 1) * u.ms
                time = time.to(u.s)
                # self.source = Source(model[default['model']], **model['param'])
                model_dict = {'name': model['name'],
                              'param': {}}
                for key, param in model.items():
                    if key != 'name':
                        _param = param.split()
                        if len(_param) > 1:
                            value = _param[0]
                            unit = u.Unit(_param[1])
                        else:
                            value = _param[0]
                            unit = None
                        if value.replace('.', '', 1).isnumeric():  # Replace allws detection of floats like '0.004'
                            value = float(value) if not float(value).is_integer() else int(value)
                            value *= unit if unit is not None else 1
                        model_dict['param'].update({key: value})

                # self.source = Source(model['name'], **model['param'])
                dist = float(basic['distance']) * u.kpc

                if basic['flavors'] and basic['flavors'].upper() not in ('DEFAULT', 'ALL'):
                    flavors = basic['flavors']  # TODO Add str-to-flavor parser
                else:
                    flavors = Flavor

                if basic['interactions'] and basic['interactions'].upper() not in ('DEFAULT', 'ALL'):
                    interactions = basic['interactions']  # TODO Add str-to-interactions parser
                else:
                    interactions = Interactions

                self._create_paramdict(model_dict, dist, flavors, basic['hierarchy'], interactions, mixing['scheme'],
                                       float(mixing['angle']), energy, time)
                self.__init__(**self.param)

                if not geomfile:
                    self._geomfile = os.path.join(os.environ['ASTERIA'],
                                                  'data/detector/Icecube_geometry.20110102.complete.txt')
                else:
                    self._geomfile = geomfile

                if not effvolfile:
                    self._effvolfile = os.path.join(os.environ['ASTERIA'],
                                                    'data/detector/effectivevolume_benedikt_AHA_normalDoms.txt')
                else:
                    self._effvolfile = effvolfile

                self.detector = Detector(self._geomfile, self._effvolfile)

        else:
            raise ValueError('Missing required arguments. Use argument `config` or `model`.')

    def _create_paramdict(self, model=None, distance=10 * u.kpc, flavors=None, hierarchy=None,
                          interactions=Interactions, mixing_scheme=None, mixing_angle=None, E=None, t=None):
        self.param.update({
            'model': model,
            'distance': distance,
            'E': E,
            't': t,
            'flavors': flavors,
            'hierarchy': hierarchy,
            'mixing_scheme': mixing_scheme,
            'mixing_angle': mixing_angle,
            'interactions': interactions,
        })

    def run(self, load_simulation=False):
        """Simulates the photonic energy per volume in the IceCube Detector or loads an existing simulation

        :param load_simulation: indicates whether or not to attempt to load an existing simulation
        :type load_simulation: bool
        :return: None
        :rtype: None
        """
        if load_simulation:
            raise NotImplementedError('Simulation loading is not currently implemented')

        self.compute_photon_spectra()
        self.compute_energy_per_vol()
        return

    def compute_photon_spectra(self):
        """Computes the spectrum of photons produced by neutrino interactions in the IceCube Detector
            Data are stored in SimulationHandler.photon_spectra
        :return: None
        :rtype: None
        """
        self._photon_spectra = {}

        for flavor in self.flavors:
            result = np.zeros(self.energy.size)
            for interaction in self.interactions:
                xs = interaction.cross_section(flavor, self.energy).to(u.m ** 2).value
                E_lep = interaction.mean_lepton_energy(flavor, self.energy).value
                photon_scaling_factor = interaction.photon_scaling_factor(flavor).value
                result += xs * E_lep * photon_scaling_factor
            self._photon_spectra.update({flavor: result * (u.m * u.m)})

    def get_combined_spectrum(self, t, E, flavor, mixing):
        """Returns mixed neutrino spectrum as a function of time and energy arising from flavor oscillations

        Parameters
        ----------
        t : astropy.quantity.Quantity
            Array of times used to perform calculation
        E : astropy.quantity.Quantity
            Array of energies used to perform calculation
        flavor : snewpy.neutrino.Flavor
            Neutrino flavor used to perform calcuation, informs selection of oscillation probability
        mixing : snewpy.flavor_transformation.FlavorTransformation
            Mixing scheme used to perform calcuation

        Returns
        -------
        spectrum : np.ndarray
            Mixed neutrino spectrum as a 2D array with dim (time, energy)
        """
        # TODO: Check that this function still works when p_surv and pc_osc are arrays
        # TODO: Simplify after adding neutrino oscillates_to property to SNEWPY
        # The cflavor "complementary flavor" is the flavor that the provided argument `flavor` oscillates to/from
        if flavor.is_neutrino:
            if flavor.is_electron:
                coeffs = mixing.prob_ee(t, E), mixing.prob_ex(t, E)
                cflavor = Flavor.NU_X
            else:
                coeffs = mixing.prob_xx(t, E), mixing.prob_xe(t, E)
                cflavor = Flavor.NU_E
        else:
            if flavor.is_electron:
                coeffs = mixing.prob_eebar(t, E), mixing.prob_exbar(t, E)
                cflavor = Flavor.NU_X_BAR
            else:
                coeffs = mixing.prob_xxbar(t, E), mixing.prob_xebar(t, E)
                cflavor = Flavor.NU_E_BAR

        nu_spectrum = np.zeros(shape=(t.size, E.size))
        for coeff, _flavor in zip(coeffs, (flavor, cflavor)):
            alpha = self.source.alpha(t, _flavor)
            meanE = self.source.meanE(t, _flavor).to(u.MeV).value

            alpha[alpha < 0] = 0
            cut = (alpha >= 0) & (meanE > 0)

            flux = self.source.flux(t[cut], _flavor).value.reshape(-1, 1)
            nu_spectrum[cut] += coeff * self.source.energy_pdf(t[cut], E, _flavor) * flux

        photon_spectrum = self._photon_spectra[flavor].to(u.m ** 2).value.reshape(1, -1)
        return nu_spectrum * photon_spectrum

    def compute_energy_per_vol(self, *, part_size=1000):
        """Compute the energy deposited in a cubic meter of ice by photons
        from SN neutrino interactions.

        Parameters
        ----------

        part_size : int
           Maximum number of time steps to compute at once. A temporary numpy array
           of size n x time.size is created and can be very memory inefficient.

        Returns
        -------
        E_per_V
           Energy per m**3 of ice deposited  by neutrinos of requested flavor
        """
        if self.time.size < 2:
            raise ValueError("Time array size <2, unable to compute energy per volume.")

        H2O_in_ice = 3.053e28  # 1 / u.m**3
        dist = self.distance.to(u.m).value  # m**2

        self._E_per_V = {}
        self._total_E_per_V = np.zeros(self.time.size)

        for flavor in self.flavors:
            print(f'Starting {flavor.name} simulation... {" " * (10 - len(flavor.name))}', end='')

            # Perform core calculation on partitions in E to regulate memory usage in vectorized function
            # Maximum usage is expected to be ~8MB
            result = np.zeros(self.time.size)
            idx = 0
            if part_size < self.time.size:
                while idx + part_size < self.time.size:
                    spectrum = self.get_combined_spectrum(self.time[idx:idx + part_size], self.energy, flavor,
                                                          self._mixing)
                    result[idx:idx + part_size] = np.trapz(spectrum, self.energy.value, axis=1)
                    idx += part_size
            spectrum = self.get_combined_spectrum(self.time[idx:], self.energy, flavor, self._mixing)
            result[idx:] = np.trapz(spectrum, self.energy.value, axis=1)
            # Add distance, density and time-binning scaling factors
            result *= H2O_in_ice / (4 * np.pi * dist**2) * np.ediff1d(self.time,
                                                                      to_end=(self.time[-1] - self.time[-2])).value
            if not flavor.is_electron:
                result *= 2
            self._E_per_V.update({flavor: result * (u.MeV / u.m / u.m / u.m)})
            self._total_E_per_V += result
            print('DONE')
        self._total_E_per_V *= (u.MeV / u.m / u.m / u.m)
        self.rebin_result(dt=self._res_dt, force_rebin=True)

    @property
    def E_per_V(self):
        """Returns dictionary of photonic energy deposition vs time for each neutrino flavor.
        This property will return None if this Simulation instance has not yet been run.
        """
        return self._E_per_V if self._E_per_V else None

    @property
    def total_E_per_V(self):
        """Returns all-flavor photonic energy deposition vs time for each neutrino flavor.
        This property will return None if this Simulation instance has not yet been run.
        """
        return self._total_E_per_V if self._total_E_per_V else None

    def avg_dom_signal(self, dt=None, flavor=None):
        """Returns estimated signal in one DOM, computed using avg DOM effective volume
        This property will return None if this Simulation instance has not yet been run.

        Parameters
        ----------
        dt : astropy.quantity.Quantity or None
            Time binning used to report signal (e.g. 2 ms).
            If None is provided, this will return the avg signal in the binning used for the simulation
        flavor : snewpy.neutrino.Flavor or None
            Neutrino flavor for which signal is calculated.
            If None is provided, this will return the avg signal from all flavors.

        Returns
        -------
        avg_signal : numpy.ndarray
            Average signal observed in one DOM as a function of time
        """
        if dt is not None:
            self.rebin_result(dt)
            if flavor is None:
                E_per_V = self._total_E_per_V_binned
            else:
                E_per_V = self._E_per_V_binned
        else:
            if flavor is None:
                E_per_V = self._total_E_per_V
            else:
                E_per_V = self._E_per_V

        effvol = 0.1654 * u.m ** 3 / u.MeV  # Simple estimation of IceCube DOM Eff. Vol.
        return effvol * E_per_V

    def rebin_result(self, dt, *, force_rebin=False):
        """Rebins the simulation results to a new time binning.

        Parameters
        ----------
        dt : astropy.quantity.Quantity
            New time binning, must be a multiple of the base binning used for the simulation.
        force_rebin : bool
            If True, perform the rebin operation, regardless of other circumstances.
            If False, only perform rebin if argument `dt` differs with current binning stored in `self._res_dt`

        Returns
        -------
        None
        """
        if self._E_per_V is None or self._total_E_per_V is None:
            raise RuntimeError("Simulation has not been executed yet, please use Simulation.run()")

        _dt = dt.to(u.s).value
        if _dt != self._res_dt.to(u.s).value or force_rebin:
            _t = self.time.to(u.s).value
            rebinfactor = int(_dt / self._sim_dt.to(u.s).value)  # TODO: Check behavior for case _res_dt % _sim_dt != 0

            self._time_binned = np.array([part[0] for part in _get_partitions(_t, part_size=rebinfactor)]) * u.s

            self._E_per_V_binned = {}
            self._total_E_per_V_binned = np.zeros_like(self._time_binned.value)

            for flavor in self.flavors:
                E_per_V_binned = np.array([np.sum(part) for part in _get_partitions(self._E_per_V[flavor].value,
                                                                                    part_size=rebinfactor)])
                self._E_per_V_binned[flavor] = E_per_V_binned * (u.MeV / u.m / u.m / u.m)
                self._total_E_per_V_binned += E_per_V_binned
            self._total_E_per_V_binned *= (u.MeV / u.m / u.m / u.m)
            self._res_dt = _dt * u.s
            self._eps_i3 = self._compute_deadtime_efficiency(domtype='i3')
            self._eps_dc = self._compute_deadtime_efficiency(domtype='dc')

    def scale_result(self, distance, force_rescale=False):
        """Rescales the simulation results to a progenitor distance.

        Parameters
        ----------
        distance : astropy.quantity.Quantity
            New progenitor, must be a multiple of the base binning used for the simulation.
        force_rescale : bool
            If True, perform the rebin operation, regardless of other circumstances.
            If False, only perform rebin if argument `dt` differs with current binning stored in `self._res_dt`

        Returns
        -------
        None
        """
        if self._E_per_V is None or self._total_E_per_V is None:
            raise RuntimeError("Simulation has not been executed yet, please use Simulation.run()")

        new_dist = distance.to(u.kpc).value
        current_dist = self.distance.to(u.kpc).value

        if new_dist != current_dist or force_rescale:
            scaling_factor = (current_dist / new_dist) ** 2
            for flavor in self.flavors:
                self._E_per_V[flavor] *= scaling_factor
                self._E_per_V_binned[flavor] *= scaling_factor
            self._total_E_per_V *= scaling_factor
            self._total_E_per_V_binned *= scaling_factor
            self.rebin_result(dt=self._res_dt, force_rebin=True)
            self.distance = new_dist * u.kpc

    def _compute_deadtime_efficiency(self, domtype='i3', *, dom_effvol=None):
        """Compute DOM deadtime efficiency factor (arises from 250 us artificial deadtime).
        From A&A 535, A109 (2011) [https://doi.org/10.1051/0004-6361/201117810]

        Parameters
        ----------
        domtype : str
            Type of IceCube DOM 'i3' is IC80 DOM, 'dc' is DeepCore DOM from the Simulation.detector member
            This argument is ignored if a specific DOM effective volume is provided.
        dom_effvol : float or np.ndarray, optional
            DOM effective volume measured in MeV / m**3 (but not stored with astropy units).
            This may either a float (for a single DOM) or an array of float (for a table of DOMs)

        Returns
        -------
        eps : float or np.ndarray
            DOM deadtime efficiency

        Notes
        -----
        This deadtime factor is calculated using the rate observed in 1s bins (hz), but the results stored
        in class members are not scaled to 1s after this function hass been run.
        """
        if dom_effvol is None:  # If dom_effvol is provided, domtype argument is unused
            if domtype == 'i3':
                dom_effvol = self.detector.i3_dom_effvol
            elif domtype == 'dc':
                dom_effvol = self.detector.dc_dom_effvol  # dc effective vol already includes relative efficiency
            else:
                raise ValueError(f"Unknown domtype: {domtype}, expected ('i3', 'dc')")

        if isinstance(dom_effvol, np.ndarray):
            # Ensures proper np broadcasting
            dom_signal = self.total_E_per_V_binned.value.reshape(-1, 1) * dom_effvol.reshape(1, -1)
        else:
            dom_signal = dom_effvol * self.total_E_per_V_binned.value
        scaling_factor = 1/self._res_dt.to(u.s).value
        return 0.87 / (1 + self.detector.deadtime * dom_signal * scaling_factor)

    @property
    def eps_i3(self):
        """Deadtime efficiency for IC80 DOMs
        """
        return self._eps_i3

    @property
    def eps_dc(self):
        return self._eps_dc

    @property
    def total_E_per_V_binned(self):
        return self._total_E_per_V_binned

    @property
    def E_per_V_binned(self):
        return self._E_per_V_binned

    @property
    def time_binned(self):
        return self._time_binned

    def detector_signal(self, dt=None, flavor=None, subdetector=None):
        """Compute signal rates observed by detector

        Parameters
        ----------
        dt : Quantity
            Time binning for hit rates (must be a multiple of base dt used for simulation)
        flavor: snewpy.neutrino.Flavor
            Flavor for which to report signal, if None is provided, all-flavor signal is reported

        Returns
        -------
        signal : numpy.ndarray
            Signal observed by the IceCube detector (or subdetector)

        Notes
        -----
        "Signal" is defined to be the expected average hit rate in a bin
        """
        self.rebin_result(dt)

        i3_total_effvol = self.detector.i3_total_effvol if subdetector != 'dc' else 0
        dc_total_effvol = self.detector.dc_total_effvol if subdetector != 'i3' else 0
        E_per_V = self.total_E_per_V_binned.value if flavor is None else self.E_per_V_binned[flavor].value

        return self.time_binned, E_per_V * (i3_total_effvol * self.eps_i3 + dc_total_effvol * self.eps_dc)

    def detector_hits(self, dt=2 * u.ms, flavor=None, subdetector=None):
        """Compute hit rates observed by detector

        Parameters
        ----------
        dt : Quantity
            Time binning for hit rates (must be a multiple of base dt used for simulation)
        flavor: snewpy.neutrino.Flavor
            Flavor for which to report signal, if None is provided, all-flavor signal is reported
        subdetector: None or str
            IceCube subdetector, must be None (Full Detector), 'i3' (IC80) or 'dc' (DeepCore)

        Returns
        -------
        hits : np.ndarray
            Hits observed by the IceCube detector (or subdetector) as a function of time
        """
        time_binned, signal = self.detector_signal(dt, flavor, subdetector)
        return time_binned, np.random.poisson(signal)

    def detector_significance(self, dt=0.5*u.s, *, method=None):
        """Returns SN triggering test statistic xi for the current neutrino lightcurve.

        Parameters
        ----------
        dt : Quantity
            Time binning for hit rates (must be a multiple of base dt used for simulation)
        method :

        Returns
        -------

        """
        self.rebin_result(dt)
        if method == 'subdetector':  # Use definition of delta_mu(_var) from SNDAQ
            return self._subdetector_significance(dt)
        elif method == 'dom':  # Scale response based on EffVol of each DOM
            return self._domwise_significance(dt)
        else:  # Use simple calculation from USSR
            return self._simple_significance(dt)

    def _simple_significance(self, dt=0.5*u.ms):
        _dt = dt.to(u.s).value
        i3_dom_bg_var = self.detector.i3_dom_bg_sig**2 * _dt
        dc_dom_bg_var = self.detector.dc_dom_bg_sig**2 * _dt

        detector_bg_var = (self.detector.n_i3_doms * i3_dom_bg_var + self.detector.n_dc_doms * dc_dom_bg_var)
        time, hits = self.detector_hits(dt)
        signi = hits / np.sqrt(detector_bg_var)
        return time, signi

    def _domwise_significance(self, dt=0.5*u.ms):
        _dt = dt.to(u.s).value
        signi = np.zeros(self._time_binned.size)

        effvol = self.detector.doms_table['effvol']
        mask_dc = self.detector.doms_table['type'] == 'dc'

        rel_eff = np.where(mask_dc, self.detector.dc_rel_eff, 1.)
        bg_mu = np.where(mask_dc, self.detector.dc_dom_bg_mu, self.detector.i3_dom_bg_mu) * _dt
        bg_sig = np.where(mask_dc, self.detector.dc_dom_bg_sig, self.detector.i3_dom_bg_sig) * np.sqrt(_dt)

        # Restrict memory usage here, maximum usage is expected to be ~10MB
        # TODO: Figure out runtime benchmark and increase part_size if necessary
        idc = np.arange(self.total_E_per_V_binned.size)
        eps = self._compute_deadtime_efficiency(dom_effvol=effvol)  # (m,n)

        for E_per_V_part, idc_part in _get_partitions(self.total_E_per_V_binned, idc, part_size=250):
            # Notes for numpy broadcasting, dim(E_per_V_part) = m, dim(effvol) = n. Aligned dims must match
            signal = E_per_V_part.value.reshape(-1, 1) * eps * effvol.reshape(1, -1)  # (m,n)=(m,1)*(m,n)*(1,n)
            hits = np.random.poisson(signal, size=signal.shape)  # (m,n)
            bg = np.random.normal(loc=bg_mu, scale=bg_sig, size=signal.shape)  # (m,n)
            rate = hits + bg  # (m,n)=(m,n)+(m,n)

            var_dmu = 1/np.sum((rel_eff**2 / bg_sig**2))  # float = sum((n,)/(n,))
            # (m,) = float*sum( ((m,n)-(m,n)) / (1,n), axis=1 )
            dmu = var_dmu * np.sum((rate - bg_mu) / bg_sig.reshape(1, - 1)**2, axis=1)
            signi[idc_part] = dmu / np.sqrt(var_dmu)
        return self._time_binned, signi

    def _subdetector_significance(self, dt=0.5*u.ms):
        _dt = dt.to(u.s).value
        i3_dom_bg_var = self.detector.i3_dom_bg_sig**2 * _dt
        dc_dom_bg_var = self.detector.dc_dom_bg_sig**2 * _dt

        time, i3_hits = self.detector_hits(dt=dt, subdetector='i3')
        _, dc_hits = self.detector_hits(dt=dt, subdetector='dc')

        i3_bg = self.detector.i3_bg(dt=dt, size=i3_hits.size)
        dc_bg = self.detector.dc_bg(dt=dt, size=dc_hits.size)
        i3_rate = i3_hits + i3_bg
        dc_rate = dc_hits + dc_bg

        # TODO: Decide whether if the background lightcurve should be saved to a data member as part of this or any
        #  similar operation
        var_dmu = 1 / ((self.detector.n_i3_doms / i3_dom_bg_var) +
                       (self.detector.n_dc_doms * self.detector.dc_rel_eff**2 / dc_dom_bg_var))
        dmu = var_dmu * ((i3_rate - i3_bg.mean()) / i3_dom_bg_var + (dc_rate - dc_bg.mean()) / dc_dom_bg_var)
        signi = dmu / np.sqrt(var_dmu)
        return time, signi

    def trigger_significance(self, dt=0.5*u.s, *, method=None):
        return self.detector_significance(dt=dt, method=method)[1].max()

    def sample_significance(self, sample_size, dt=0.5*u.s, distance=10*u.kpc, method=None):
        self.scale_result(distance)
        return np.array([self.trigger_significance(dt, method=method) for _ in range(sample_size)])


def _get_partitions(*args, part_size=1000):
    if len(args) > 1:
        if not all(len(x) == len(args[0]) for x in args):
            raise ValueError(f'Inputs must have same size, given sizes ({", ".join((str(len(x)) for x in args))})')
    total_size = len(args[0])
    if part_size > total_size:
        yield tuple(x for x in args) if len(args) > 1 else args[0]
    else:
        idx = 0
        while idx + part_size < total_size:
            yield tuple(x[idx:idx + part_size] for x in args) if len(args) > 1 else args[0][idx:idx + part_size]
            idx += part_size
        yield tuple(x[idx:] for x in args) if len(args) > 1 else args[0][idx:]
