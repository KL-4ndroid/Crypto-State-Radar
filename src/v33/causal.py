"""Explicit timestamp units and past-only reference calculations."""
from datetime import datetime,timezone
import math
from .contracts import stamp


def epoch_time(value,unit):
    if unit not in {'ms','us'} or isinstance(value,bool) or not isinstance(value,int):
        raise ValueError('explicit integer ms/us timestamp required')
    return stamp(datetime.fromtimestamp(value/({'ms':1000,'us':1000000}[unit]),timezone.utc))


def past_percentile(observations, evaluation_time, percentile, minimum_samples):
    """Rows must carry actual availability, not just exchange event timestamps."""
    when=stamp(evaluation_time)
    if not 0<=percentile<=100 or minimum_samples<1:raise ValueError('invalid percentile policy')
    values=[]
    for row in observations:
        if stamp(row['asof_time'])>=when or stamp(row['availability_time'])>when:continue
        v=row['value']
        if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):values.append(v)
    if len(values)<minimum_samples:return None
    values.sort();rank=(len(values)-1)*percentile/100
    lower=int(rank);upper=min(lower+1,len(values)-1)
    return values[lower]+(values[upper]-values[lower])*(rank-lower)
