
angular.module('pgdb.corpora')
    .service('Corpora', function ($http, __env) {
    var base_url = __env.apiUrl + 'corpora/';
    var Corpora = {};

    Corpora.all = function () {
        return $http.get(base_url);
    };

    Corpora.one = function (id) {
        return $http.get(base_url + id + '/');
    };

    Corpora.importCorpus = function(id){
        return $http.post(base_url + id +'/import_corpus/', {})
    };

    Corpora.status = function (id) {
        return $http.get(base_url + id + '/status/');
    };

    Corpora.hierarchy = function (id) {
        return $http.get(base_url + id + '/hierarchy/');
    };

    Corpora.phones = function (id) {
      return $http.get(base_url + id + '/phones/');
    };

    Corpora.phone_set = function (id) {
      return $http.get(base_url + id + '/phone_set/');
    };

    Corpora.speakers = function (id){
        return $http.get(base_url + id + '/speakers/');
    };

    Corpora.discourses = function (id){
        return $http.get(base_url + id + '/discourses/');
    };

    Corpora.discourse_property_options = function(id){
        return $http.get(base_url + id + '/discourses/properties');
    };

    Corpora.utterances = function (id) {
        return $http.get(base_url + id + '/utterances/');
    };

    Corpora.pitch_tracks = function (id) {
        return $http.get(base_url + id + '/pitch_tracks/');
    };

    Corpora.update = function (updatedCorpus) {
        return $http.put(base_url + updatedCorpus.id, updatedCorpus);
    };

    Corpora.delete = function (id) {
        return $http.delete(base_url + id + '/');
    };

    Corpora.addOne = function (newCorpus) {
        return $http.post(base_url, newCorpus)
    };

    return Corpora;
});
