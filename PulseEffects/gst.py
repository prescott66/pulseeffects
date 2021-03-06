# -*- coding: utf-8 -*-

import logging

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst


Gst.init(None)


class GstEffects(GObject.GObject):

    __gsignals__ = {
        'new_level_before_limiter': (GObject.SIGNAL_RUN_FIRST, None,
                                     (float, float)),
        'new_level_after_limiter': (GObject.SIGNAL_RUN_FIRST, None,
                                    (float, float)),
        'new_autovolume': (GObject.SIGNAL_RUN_FIRST, None,
                           (float,)),
        'new_level_after_compressor': (GObject.SIGNAL_RUN_FIRST, None,
                                       (float, float)),
        'new_compressor_gain_reduction': (GObject.SIGNAL_RUN_FIRST, None,
                                          (float,)),
        'new_limiter_attenuation': (GObject.SIGNAL_RUN_FIRST, None,
                                    (float,)),
        'new_level_after_reverb': (GObject.SIGNAL_RUN_FIRST, None,
                                   (float, float)),
        'new_level_after_eq': (GObject.SIGNAL_RUN_FIRST, None,
                               (float, float)),
        'new_spectrum': (GObject.SIGNAL_RUN_FIRST, None,
                         (object,))
    }

    def __init__(self, sampling_rate):
        GObject.GObject.__init__(self)

        self.old_limiter_attenuation = 0
        self.old_compressor_gain_reduction = 0
        self.rate = sampling_rate
        self.max_spectrum_freq = 20000  # Hz
        self.spectrum_nbands = 400
        self.spectrum_freqs = []
        self.spectrum_nfreqs = 0
        self.spectrum_threshold = -100  # dB

        self.autovolume_enabled = False
        self.is_playing = False

        self.log = logging.getLogger('PulseEffects')

        self.calc_spectrum_freqs()

        self.pipeline = self.build_pipeline()

        # Create bus to get events from GStreamer pipeline
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self.on_message)

    def build_pipeline(self):
        pipeline = Gst.Pipeline()

        self.audio_src = Gst.ElementFactory.make('pulsesrc', 'audio_src')

        source_caps = Gst.ElementFactory.make("capsfilter", None)

        self.limiter = Gst.ElementFactory.make(
            'ladspa-fast-lookahead-limiter-1913-so-fastlookaheadlimiter', None)

        self.compressor = Gst.ElementFactory.make(
            'ladspa-sc4-1882-so-sc4', None)

        self.freeverb = Gst.ElementFactory.make('freeverb', None)

        self.equalizer_preamp = Gst.ElementFactory.make('volume', None)

        self.equalizer = Gst.ElementFactory.make('equalizer-nbands', None)

        self.audio_sink = Gst.ElementFactory.make('pulsesink', 'audio_sink')

        level_before_limiter = Gst.ElementFactory.make(
            'level', 'level_before_limiter')
        level_after_limiter = Gst.ElementFactory.make(
            'level', 'level_after_limiter')
        level_after_compressor = Gst.ElementFactory.make(
            'level', 'level_after_compressor')
        level_after_reverb = Gst.ElementFactory.make(
            'level', 'level_after_reverb')
        level_after_eq = Gst.ElementFactory.make('level', 'level_after_eq')

        autovolume = Gst.ElementFactory.make('level', 'autovolume')

        spectrum = Gst.ElementFactory.make('spectrum', 'spectrum')

        self.audio_src.set_property('volume', 1.0)
        self.audio_src.set_property('mute', False)
        self.audio_src.set_property('provide-clock', False)
        self.audio_src.set_property('slave-method', 1)  # re-timestamp

        caps = ['audio/x-raw', 'format=F32LE',
                'rate=' + str(self.rate), 'channels=2']

        src_caps = Gst.Caps.from_string(",".join(caps))
        source_caps.set_property("caps", src_caps)

        self.audio_sink.set_property('volume', 1.0)
        self.audio_sink.set_property('mute', False)

        autovolume.set_property('interval', 2000000000)  # 2 seconds

        self.equalizer.set_property('num-bands', 15)

        self.eq_band0 = self.equalizer.get_child_by_index(0)
        self.eq_band1 = self.equalizer.get_child_by_index(1)
        self.eq_band2 = self.equalizer.get_child_by_index(2)
        self.eq_band3 = self.equalizer.get_child_by_index(3)
        self.eq_band4 = self.equalizer.get_child_by_index(4)
        self.eq_band5 = self.equalizer.get_child_by_index(5)
        self.eq_band6 = self.equalizer.get_child_by_index(6)
        self.eq_band7 = self.equalizer.get_child_by_index(7)
        self.eq_band8 = self.equalizer.get_child_by_index(8)
        self.eq_band9 = self.equalizer.get_child_by_index(9)
        self.eq_band10 = self.equalizer.get_child_by_index(10)
        self.eq_band11 = self.equalizer.get_child_by_index(11)
        self.eq_band12 = self.equalizer.get_child_by_index(12)
        self.eq_band13 = self.equalizer.get_child_by_index(13)
        self.eq_band14 = self.equalizer.get_child_by_index(14)

        # It seems to me that there is a bug in the low shelf filter.
        # When we increase the lower shelf gain higher frequencies
        # are attenuated. Setting the first band to peak type instead of
        # shelf fixes this.

        self.eq_band0.set_property('type', 0)  # 0: peak type

        spectrum.set_property('bands', self.spectrum_nbands)
        spectrum.set_property('threshold', self.spectrum_threshold)

        self.eq_highpass = Gst.ElementFactory.make('audiocheblimit', None)
        self.eq_highpass.set_property('mode', 'high-pass')
        self.eq_highpass.set_property('type', 1)
        self.eq_highpass.set_property('ripple', 0)

        self.eq_lowpass = Gst.ElementFactory.make('audiocheblimit', None)
        self.eq_lowpass.set_property('mode', 'low-pass')
        self.eq_lowpass.set_property('type', 1)
        self.eq_lowpass.set_property('ripple', 0)

        pipeline.add(self.audio_src)
        pipeline.add(source_caps)
        pipeline.add(level_before_limiter)
        pipeline.add(self.limiter)
        pipeline.add(level_after_limiter)
        pipeline.add(autovolume)
        pipeline.add(self.compressor)
        pipeline.add(level_after_compressor)
        pipeline.add(self.freeverb)
        pipeline.add(level_after_reverb)
        pipeline.add(self.eq_highpass)
        pipeline.add(self.eq_lowpass)
        pipeline.add(self.equalizer_preamp)
        pipeline.add(self.equalizer)
        pipeline.add(level_after_eq)
        pipeline.add(spectrum)
        pipeline.add(self.audio_sink)

        self.audio_src.link(source_caps)
        source_caps.link(level_before_limiter)
        level_before_limiter.link(self.limiter)
        self.limiter.link(level_after_limiter)
        level_after_limiter.link(autovolume)
        autovolume.link(self.compressor)
        self.compressor.link(level_after_compressor)
        level_after_compressor.link(self.freeverb)
        self.freeverb.link(level_after_reverb)
        level_after_reverb.link(self.eq_highpass)
        self.eq_highpass.link(self.eq_lowpass)
        self.eq_lowpass.link(self.equalizer_preamp)
        self.equalizer_preamp.link(self.equalizer)
        self.equalizer.link(level_after_eq)
        level_after_eq.link(spectrum)
        spectrum.link(self.audio_sink)

        return pipeline

    def print_eq_freqs(self):
        print(self.eq_band0.get_property('freq'))
        print(self.eq_band1.get_property('freq'))
        print(self.eq_band2.get_property('freq'))
        print(self.eq_band3.get_property('freq'))
        print(self.eq_band4.get_property('freq'))
        print(self.eq_band5.get_property('freq'))
        print(self.eq_band6.get_property('freq'))
        print(self.eq_band7.get_property('freq'))
        print(self.eq_band8.get_property('freq'))
        print(self.eq_band9.get_property('freq'))
        print(self.eq_band10.get_property('freq'))
        print(self.eq_band11.get_property('freq'))
        print(self.eq_band12.get_property('freq'))
        print(self.eq_band13.get_property('freq'))
        print(self.eq_band14.get_property('freq'))

        # print(self.eq_band0.get_property('bandwidth'))

    def set_state(self, state):
        if state == 'ready':
            s = self.pipeline.set_state(Gst.State.READY)

            if s == Gst.StateChangeReturn.FAILURE:
                self.log.critical("Could not set Gstreamer pipeline to ready")
                return False
            else:
                self.is_playing = False
                return True
        elif state == 'paused':
            s = self.pipeline.set_state(Gst.State.PAUSED)

            if s == Gst.StateChangeReturn.FAILURE:
                self.log.error("Failed to pause Gstreamer pipeline")
                return False
            else:
                self.is_playing = False
                return True
        elif state == 'playing':
            s = self.pipeline.set_state(Gst.State.PLAYING)

            if s == Gst.StateChangeReturn.FAILURE:
                self.log.critical("Playing Gstreamer pipeline has failed")
                return False
            else:
                self.is_playing = True
                return True
        elif state == 'null':
            s = self.pipeline.set_state(Gst.State.NULL)

            if s == Gst.StateChangeReturn.FAILURE:
                self.log.error("Could not stop Gstreamer pipeline")
                return False
            else:
                self.is_playing = False
                return True

    def calc_spectrum_freqs(self):
        self.spectrum_freqs = []

        for i in range(self.spectrum_nbands):
            freq = self.rate * (0.5 * i + 0.25) / self.spectrum_nbands

            if freq > self.max_spectrum_freq:
                break

            self.spectrum_freqs.append(freq)

        self.spectrum_nfreqs = len(self.spectrum_freqs)

        self.log.info('(min, max) spectrum frequencies: ' +
                      '(' + str(min(self.spectrum_freqs)) + ', ' +
                      str(max(self.spectrum_freqs)) + ')' +
                      ' these values are sampling rate dependent')

    def auto_gain(self, mean):
        threshold = -12
        delta = 1
        mean = int(mean)

        if mean > threshold + delta:
            gain = self.limiter.get_property('input-gain')

            if gain - 1 >= -20:
                gain = gain - 1

                self.emit('new_autovolume', gain)
        elif mean < threshold - delta:
            gain = self.limiter.get_property('input-gain')

            if gain + 1 <= 20:
                gain = gain + 1

                self.emit('new_autovolume', gain)

    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            self.log.error(msg.parse_error())
            self.log.error(msg.parse_error_details())

            self.set_state('null')
        elif msg.type == Gst.MessageType.WARNING:
            self.log.warning(msg.parse_warning())
            self.log.warning(msg.parse_warning_details())
        elif msg.type == Gst.MessageType.INFO:
            self.log.info(msg.parse_info())
            self.log.info(msg.parse_info_details())
        elif msg.type == Gst.MessageType.LATENCY:
            plugin = msg.src.get_name()

            if plugin == 'audio_sink':
                latency = msg.src.get_property('latency-time')
                self.log.info('pulsesink latency [us]: ' + str(latency))
            elif plugin == 'audio_src':
                latency = msg.src.get_property('actual-latency-time')
                self.log.info('pulsesrc latency [us]: ' + str(latency))
        elif msg.type == Gst.MessageType.ELEMENT:
            plugin = msg.src.get_name()

            if plugin == 'level_before_limiter':
                peak = msg.get_structure().get_value('peak')

                self.emit('new_level_before_limiter', peak[0], peak[1])
            elif plugin == 'level_after_limiter':
                peak = msg.get_structure().get_value('peak')

                self.emit('new_level_after_limiter', peak[0], peak[1])

                attenuation = round(self.limiter.get_property('attenuation'))

                if attenuation != self.old_limiter_attenuation:
                    self.old_limiter_attenuation = attenuation

                    self.emit('new_limiter_attenuation', attenuation)
            elif plugin == 'autovolume':
                if self.autovolume_enabled:
                    peak = msg.get_structure().get_value('peak')

                    mean = 0.5 * (peak[0] + peak[1])

                    if mean > -40:
                        self.auto_gain(mean)

            elif plugin == 'level_after_compressor':
                peak = msg.get_structure().get_value('peak')

                self.emit('new_level_after_compressor', peak[0], peak[1])

                gain_reduction = round(
                    self.compressor.get_property('gain-reduction'))

                if gain_reduction != self.old_compressor_gain_reduction:
                    self.old_compressor_gain_reduction = gain_reduction

                    self.emit('new_compressor_gain_reduction', gain_reduction)
            elif plugin == 'level_after_reverb':
                peak = msg.get_structure().get_value('peak')

                self.emit('new_level_after_reverb', peak[0], peak[1])
            elif plugin == 'level_after_eq':
                peak = msg.get_structure().get_value('peak')

                self.emit('new_level_after_eq', peak[0], peak[1])
            elif plugin == 'spectrum':
                magnitudes = msg.get_structure().get_value('magnitude')

                if len(magnitudes) > 0:
                    magnitudes = magnitudes[:self.spectrum_nfreqs]

                    max_mag = max(magnitudes)
                    min_mag = self.spectrum_threshold

                    if max_mag > min_mag:
                        magnitudes = [(min_mag - v) / min_mag
                                      for v in magnitudes]

                        self.emit('new_spectrum', magnitudes)
        return True

    def set_source_monitor_name(self, name):
        self.audio_src.set_property('device', name)

    def set_output_sink_name(self, name):
        self.audio_sink.set_property('device', name)

    def get_configured_src_device(self):
        return self.audio_src.get_property('device')

    def get_current_src_device(self):
        return self.audio_src.get_property('current-device')

    def init_buffer_time(self, value):
        self.audio_src.set_property('buffer-time', value)
        self.audio_sink.set_property('buffer-time', value)

    def set_buffer_time(self, value):
        self.set_state('ready')
        self.audio_src.set_property('buffer-time', value)
        self.audio_sink.set_property('buffer-time', value)
        self.set_state('playing')

    def set_autovolume_state(self, value):
        self.autovolume_enabled = value

    def set_limiter_input_gain(self, value):
        self.limiter.set_property('input-gain', value)

    def set_limiter_limit(self, value):
        self.limiter.set_property('limit', value)

    def set_limiter_release_time(self, value):
        self.limiter.set_property('release-time', value)

    def set_compressor_measurement_type(self, value):
        self.compressor.set_property('rms-peak', value)

    def set_compressor_attack(self, value):
        self.compressor.set_property('attack-time', value)

    def set_compressor_release(self, value):
        self.compressor.set_property('release-time', value)

    def set_compressor_threshold(self, value):
        self.compressor.set_property('threshold-level', value)

    def set_compressor_ratio(self, value):
        self.compressor.set_property('ratio', value)

    def set_compressor_knee(self, value):
        self.compressor.set_property('knee-radius', value)

    def set_compressor_makeup(self, value):
        self.compressor.set_property('makeup-gain', value)

    def set_reverb_room_size(self, value):
        self.freeverb.set_property('room-size', value)

    def set_reverb_damping(self, value):
        self.freeverb.set_property('damping', value)

    def set_reverb_width(self, value):
        self.freeverb.set_property('width', value)

    def set_reverb_level(self, value):
        self.freeverb.set_property('level', value)

    def set_eq_preamp(self, value):
        self.equalizer_preamp.set_property('volume', value)

    def set_eq_band0(self, value):
        self.eq_band0.set_property('gain', value)

    def set_eq_band1(self, value):
        self.eq_band1.set_property('gain', value)

    def set_eq_band2(self, value):
        self.eq_band2.set_property('gain', value)

    def set_eq_band3(self, value):
        self.eq_band3.set_property('gain', value)

    def set_eq_band4(self, value):
        self.eq_band4.set_property('gain', value)

    def set_eq_band5(self, value):
        self.eq_band5.set_property('gain', value)

    def set_eq_band6(self, value):
        self.eq_band6.set_property('gain', value)

    def set_eq_band7(self, value):
        self.eq_band7.set_property('gain', value)

    def set_eq_band8(self, value):
        self.eq_band8.set_property('gain', value)

    def set_eq_band9(self, value):
        self.eq_band9.set_property('gain', value)

    def set_eq_band10(self, value):
        self.eq_band10.set_property('gain', value)

    def set_eq_band11(self, value):
        self.eq_band11.set_property('gain', value)

    def set_eq_band12(self, value):
        self.eq_band12.set_property('gain', value)

    def set_eq_band13(self, value):
        self.eq_band13.set_property('gain', value)

    def set_eq_band14(self, value):
        self.eq_band14.set_property('gain', value)

    def set_eq_highpass_cutoff_freq(self, value):
        self.eq_highpass.set_property('cutoff', value)

    def set_eq_highpass_poles(self, value):
        self.eq_highpass.set_property('poles', value)

    def set_eq_lowpass_cutoff_freq(self, value):
        self.eq_lowpass.set_property('cutoff', value)

    def set_eq_lowpass_poles(self, value):
        self.eq_lowpass.set_property('poles', value)
