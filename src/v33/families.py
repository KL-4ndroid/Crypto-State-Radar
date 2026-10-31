"""Score availability is separate from evidence breadth. Inputs are scored features."""
import math


def numeric(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def aggregate(values, parameters):
    families = {}
    for name, components in parameters['component_weights'].items():
        if not components or any(not numeric(w) or w <= 0 for w in components.values()):
            raise ValueError('component weights must be finite and positive')
        available=[]
        for component,weight in components.items():
            value=values.get(name+'.'+component)
            if value is None:
                continue
            if not numeric(value) or not 0 <= value <= 100:
                raise ValueError('component score must be in [0,100]')
            available.append((value,weight))
        mass=sum(w for _,w in available)
        coverage=mass/sum(components.values())
        status='UNAVAILABLE' if not mass else 'VALID' if coverage>=parameters['family_valid_coverage'] else 'PARTIAL'
        score=sum(v*w for v,w in available)/mass if mass else None
        families[name]={'score':score,'coverage':coverage,'status':status,
                        'strong':status=='VALID' and score>=parameters['strong_family_score']}
    valid={n:r for n,r in families.items() if r['status']=='VALID'}
    weights=parameters['family_weights']
    if set(families)!=set(weights) or any(not numeric(w) or w<=0 for w in weights.values()):
        raise ValueError('all family weights must be finite and positive')
    # Breadth cardinalities are the V3.3 contract, not the old four-family score rule.
    breadth='FULL' if len(valid)>=4 else 'REDUCED' if len(valid)==3 else 'INSUFFICIENT'
    score=sum(r['score']*weights[n] for n,r in valid.items())/sum(weights[n] for n in valid) if len(valid)>=3 else None
    return {'families':families,'distribution_score':score,'score_breadth':breadth,
            'strong_families':sum(r['strong'] for r in valid.values())}
