angular.module("logout", ['iscan.auth']).controller("LogoutCtrl", [
    '$scope', '$rootScope', '$state',  'djangoAuth', function ($scope, $rootScope, $state, djangoAuth) {
        $scope.state = $state;

    djangoAuth.logout();
    return $state.go("home");
    }
]);
