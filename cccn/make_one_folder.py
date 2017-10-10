import obspy
import subprocess
import os
from os.path import join, basename
import numpy as np
from obspy.signal.util import next_pow_2
from scipy import fftpack
import glob
from multiprocessing.dummy import Pool as ThreadPool 
from itertools import repeat
from subprocess import Popen, PIPE
import time

def smooth(x, half_len=5,window='flat'):
    """smooth the data using a window with requested size.

    This method is based on the convolution of a scaled window with the signal.
    The signal is prepared by introducing reflected copies of the signal
    (with the window size) in both ends so that transient parts are minimized
    in the begining and end part of the output signal.

    input:
        x: the input signal
        helf_len: the half dimension of the smoothing window; should be an odd integer
        window: the type of window from 'flat', 'hanning', 'hamming', 'bartlett', 'blackman'
            flat window will produce a moving average smoothing.

    output:
        the smoothed signal

    example:

    t=linspace(-2,2,0.1)
    x=sin(t)+randn(len(t))*0.1
    y=smooth(x)

    see also:

    numpy.hanning, numpy.hamming, numpy.bartlett, numpy.blackman, numpy.convolve
    scipy.signal.lfilter

    TODO: the window parameter could be the window itself if an array instead of a string
    NOTE: length(output) != length(input), to correct this: return y[(window_len/2-1):-(window_len/2)] instead of just y.
    """

    window_len = 2*half_len+1

    if x.ndim != 1:
        raise ValueError("smooth only accepts 1 dimension arrays.")

    if x.size < window_len:
        raise ValueError("Input vector needs to be bigger than window size.")

    if window_len<3:
        return x

    if not window in ['flat', 'hanning', 'hamming', 'bartlett', 'blackman']:
        raise ValueError("Window is on of 'flat', 'hanning', 'hamming', 'bartlett', 'blackman'")    
    
    s=np.r_[x[window_len-1:0:-1],x,x[-1:-window_len:-1]]
    #print(len(s))
    if window == 'flat': #moving average
        w=np.ones(window_len,'d')
    else:
        w=eval('numpy.'+window+'(window_len)')

    y=np.convolve(w/w.sum(),s,mode='valid')
    return  y[half_len:-half_len]

def whiten(data, Nfft, delta, f1, f2, f3, f4):
    """This function takes 1-dimensional *data* timeseries array,
    goes to frequency domain using fft, whitens the amplitude of the spectrum
    in frequency domain between *freqmin* and *freqmax*
    and returns the whitened fft.

    :type data: :class:`numpy.ndarray`
    :param data: Contains the 1D time series to whiten
    :type Nfft: int
    :param Nfft: The number of points to compute the FFT
    :type delta: float
    :param delta: The sampling frequency of the `data`
    :type freqmin: float
    :param freqmin: The lower frequency bound
    :type freqmax: float
    :param freqmax: The upper frequency bound
    :type plot: bool
    :param plot: Whether to show a raw plot of the action (default: False)

    :rtype: :class:`numpy.ndarray`
    :returns: The FFT of the input trace, whitened between the frequency bounds
"""
    dom = 1/delta/Nfft
    nt1 = int(f1/dom)
    nt2 = int(f2/dom)
    nt3 = int(f3/dom)
    nt4 = int(f4/dom)

    FFTRawSign = fftpack.fft(data, Nfft)

    FFTRawSign /= smooth(np.abs(FFTRawSign), half_len=20)
    # Left tapering:
    FFTRawSign[0:nt1] *= 0
    FFTRawSign[nt1:nt2] = np.cos(np.linspace(np.pi / 2., np.pi, nt2 - nt1)) ** 2 * np.exp(1j * np.angle(FFTRawSign[nt1:nt2]))
    #FFTRawSign[nt1:nt2] = np.cos(np.linspace(np.pi / 2., np.pi, nt2 - nt1)) ** 2 * FFTRawSign[nt1:nt2]
    # Pass band:
    FFTRawSign[nt2:nt3] = np.exp(1j * np.angle(FFTRawSign[nt2:nt3]))
    # Right tapering:
    FFTRawSign[nt3:nt4] = np.cos(np.linspace(0., np.pi / 2., nt4 - nt3)) ** 2 * np.exp(1j * np.angle(FFTRawSign[nt3:nt4]))
    #FFTRawSign[nt3:nt4] = np.cos(np.linspace(0., np.pi / 2., nt4 - nt3)) ** 2 * FFTRawSign[nt3:nt4]
    FFTRawSign[nt4:Nfft+1] *= 0
    
    # Hermitian symmetry (because the input is real)
    FFTRawSign[int(-Nfft/2+1):] = FFTRawSign[1:int(Nfft/2)].conjugate()[::-1]
    
    return FFTRawSign, dom

def transf_hinet(folder,suffix, dt, ch=['U']):
    global stations
    freq = 1/dt/2-0.1
    sacfiles = glob.glob(join(folder,"*."+suffix))
    stations = [basename(sac).split('.')[0]+"."+basename(sac).split('.')[1] for sac in sacfiles]
    stations = list(set(stations))
    os.putenv("SAC_DISPLAY_COPYRIGHT", '0')
    p = subprocess.Popen(['sac'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    s = ""
    for sta in stations:
        for cname in ch:
            sacfiles = glob.glob(join(folder,"%s.%s.%s" % (sta, cname, suffix)))
            if len(sacfiles) == 1:
                ismerge = False
                sacfile = sacfiles[0]
            else:
                ismerge = True
                sacfile = ""
                for sac in sacfiles:
                    sacfile += " "+sac
            resfile = glob.glob(sacfile+"_PZ")[0]
            s += "r %s\n" % sacfile
            s += "rmean;rtr\n"
            if ismerge:
                s += "merge g z o a\n"
            s += "lp c %f\n" % freq
            s += "interp delta %6.3f\n" % dt
            s += "transfer FROM POLEZERO SUBTYPE %s TO VEL freq 0.05 0.08 2 3\n" % (resfile)
            s += "rmean;rtr\n"
            s += "ch kstnm %s\n" % sta
            s += "w %s/%s.BH%s\n" % (folder, sta, cname)
    s += "q\n"
    p.communicate(s.encode())

def make_seedresp(folder, tr, units='VEL'):
    if not units in ['DIS', 'VEL', 'ACC']:
        raise ValueError("Units to return response must be in ('DIS', 'VEL' or ACC)")
    filename=join(folder, '.'.join(['RESP', tr.stats.network, tr.stats.station, tr.stats.location, tr.stats.channel]))
    seedresp = {'filename': filename,
                'units': units}
    return seedresp

def transf(folder, suffix, dt, ch=['Z']):
    global st
    pre_filt = (0.005, 0.007, 4, 5)
    st = obspy.read(join(folder,'*.BH['+''.join(ch)+'].*.'+suffix))
    st.detrend('linear')
    st.detrend('constant')
    st.merge(1, 0)
    sample_rate = 1/dt
    st.resample(sample_rate)
    for tr in st:
        seedresp = make_seedresp(folder, tr)
        tr.simulate(paz_remove=None, pre_filt=pre_filt, seedresp=seedresp)
    st.plot()
    '''
    for sta in stations:
        for cname in ch:
            sacfiles = glob.glob(join(folder,"*.%s.%s.%s.BH%s.*.%s" % (sta[0], sta[1], sta[2], cname, suffix)))
            sacfile = ''
            if len(sacfiles) == 1:
                ismerge = False
                sacfile = sacfiles[0]
            elif len(sacfiles) > 1:
                ismerge = True
                for sac in sacfiles:
                    sacfile += " "+sac
            else:
                continue
            try:
                resfile = glob.glob(join(folder,"SAC_PZs_%s_%s_BH%s_%s" % (sta[0],sta[1],cname,sta[2])))[0]
            except:
                continue
            s += "r %s\n" % sacfile
            s += "rmean;rtr\n"
            if ismerge:
                s += "merge g z o a\n"
            s += "lp c %f\n" % freq
            s += "interp delta %6.3f\n" % dt
            s += "transfer FROM POLEZERO SUBTYPE %s TO VEL freq 0.005 0.007 4 5\n" % (resfile)
            s += "rmean;rtr\n"
            s += "ch kstnm %s\n" % (sta[0]+"."+sta[1])
            s += "w %s/%s.%s.%s.BH%s\n" % (folder, sta[0], sta[1], sta[2],cname)
    s += "q\n"
    p.communicate(s.encode())
'''

def perwhiten(dt, wlen, cuttime1,  cuttime2, reftime, f1,f2,f3,f4):
    global fft_all, st
    nft = int(next_pow_2((cuttime2 - cuttime1)/dt))
    nwlen = int(wlen/dt)
    fft_all = obspy.Stream()
    '''
    scname = '['
    for cname in ch:
        scname += cname
    scname += ']'
    '''
    for tr in st:
        #------- cut waveform ------ 
        cutbtime = reftime+cuttime1
        cutetime = reftime+cuttime2
        if tr.stats.starttime > cutbtime or tr.stats.endtime < cutetime:
            continue
        tr.trim(cutbtime, cutetime)
        #----------normalize----------
        if wlen == 0:
            tr.data /= np.abs(tr.data)
        elif wlen > 0:
            tr.data /= smooth(np.abs(tr.data),half_len=nwlen)
        else:
            raise ValueError("Half window length must be greater than zero")
        #tr.write(join(folder,"%s.%s.%s.BHZ.norm" % (tr.stats.network, tr.stats.station, tr.stats.location)), "SAC")
        #----------- Whiten -----------
        (tr.data,tr.stats.delta) = whiten(tr.data, nft, dt, f1, f2, f3, f4)
        #-------write spec to array --------
        fft_all.append(tr)
        '''
        tr1 = tr.copy()
        tr1.data = tr.data.real
        tr1.write(join(folder,"ft.%s.%s.%s.BHZ.norm.rl" % (tr.stats.network, tr.stats.station, tr.stats.location)), "SAC")
        tr2 = tr.copy()
        tr2.data = tr.data.imag
        tr2.write(join(folder,"ft.%s.%s.%s.BHZ.norm.im" % (tr.stats.network, tr.stats.station, tr.stats.location)), "SAC")
        '''
    return len(fft_all)

def compute_cc(idxij, nts, mid_pos, lag, cor):
    global fft_all,outpath
    ccf = fftpack.ifft(fft_all[idxij[0]].data*np.conj(fft_all[idxij[1]].data), nts).real
    cor.data = fftpack.ifftshift(ccf)[mid_pos-lag:mid_pos+lag+1]
    cor.stats.station = fft_all[idxij[0]].stats.station
    cor.stats.sac.stla = fft_all[idxij[0]].stats.sac.stla
    cor.stats.sac.stlo = fft_all[idxij[0]].stats.sac.stlo
    cor.stats.sac.kevnm = fft_all[idxij[1]].stats.station
    cor.stats.sac.evla = fft_all[idxij[1]].stats.sac.stla
    cor.stats.sac.evlo = fft_all[idxij[1]].stats.sac.stlo
    cor.stats.channel = fft_all[idxij[0]].stats.channel+fft_all[idxij[1]].stats.channel
    cor.stats.sac.b = -lag
    cor.write(join(outpath, "COR_%s.%s_%s.%s.SAC" % 
        (fft_all[idxij[0]].stats.station,fft_all[idxij[0]].stats.channel,
        fft_all[idxij[1]].stats.station, fft_all[idxij[1]].stats.channel)),"SAC")

def docc(folder_name, nt, dt, finalcut, reftime, f2,f3, node):
    global fft_all, outpath
    pool = ThreadPool(node)
    outpath = join(folder_name,"%sto%s_COR" % (str(f2),str(f3)))
    if not os.path.exists(outpath):
        os.makedirs(outpath)
    ns = len(fft_all)
    nts = (fft_all[0].stats.npts)
    lag = int(finalcut/dt)
    mid_pos = int(nts/2)
#   tcorr = np.arange(-nts + 1, nts)
#   dn = np.where(np.abs(tcorr) <= lag)[0]
    cor = fft_all[0].copy()
    cor.stats.delta = dt
    cor.stats.starttime = reftime
    sta_pair = []
    idx_lst = []
    for i in np.arange(ns-1):
        for j in np.arange(i+1,ns):
            if fft_all[i].stats.station == fft_all[j].stats.station:
                continue
            sta_pair.append("%s.%s_%s.%s" % 
                (fft_all[i].stats.station,fft_all[i].stats.channel,
                fft_all[j].stats.station,fft_all[j].stats.channel))
            idx_lst.append([i, j])
    t=time.clock()
    results = pool.starmap(compute_cc, zip(idx_lst, repeat(nts), repeat(mid_pos), repeat(lag), repeat(cor)))
    print("%d station pair using %d node:" % (len(sta_pair), node), (time.clock()-t), "s")
    pool.close()
    pool.join()
    return sta_pair

if __name__ == '__main__':
    folder = '/home/xumj/CCCN/example/2016.01.01'
    suf = 'SAC'
    transf(folder,suf,0.01)
