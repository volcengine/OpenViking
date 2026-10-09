import common from './common'
import connect from './connect'
import enums from './enums'
import keys from './keys'
import overview from './overview'
import profiles from './profiles'
import requests from './requests'
import upstreams from './upstreams'

const gateway = {
  ...common,
  enums,
  overview,
  upstreams,
  profiles,
  keys,
  requests,
  connect,
}

export default gateway
