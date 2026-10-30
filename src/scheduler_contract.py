"""The production cadence shared by provisioning and read-only acceptance."""
CRONS = {"full_scan":"7 * * * *", "drain":"3-58/5 * * * *", "validate":"4-59/5 * * * *"}
DEMAND_CRONS = {"full_scan":"7 * * * *", "repair":"2,17,32,47 * * * *"}


def scheduled_minute(operation, minute):
    field, *rest = CRONS[operation].split()
    if rest != ["*", "*", "*", "*"]:
        raise ValueError("acceptance requires an explicitly supported cadence")
    if field.isdigit():
        return minute == int(field)
    bounds, step = field.split('/')
    start, end = map(int,bounds.split('-'))
    return start <= minute <= end and (minute-start)%int(step)==0
