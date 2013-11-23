# coding=utf-8
import json
import urllib
import re
import socket

from cghub.util import fnmatch
from boto import ec2, s3, iam, sns, sqs
from boto.s3.key import Key as S3Key
from boto.exception import S3ResponseError, BotoServerError
from boto.s3.connection import S3Connection
from boto.sqs.connection import SQSConnection
from boto.sns.connection import SNSConnection
from boto.ec2.connection import EC2Connection
from boto.iam.connection import IAMConnection
from cghub.util import memoize

from cghub.cloud.lib.message import Message
from cghub.cloud.lib.util import ec2_keypair_fingerprint, UserError


class Context( object ):
    """
    Encapsulates all EC2-specific settings used by components in this project
    """
    s3_bucket_name = 'cghub-cloud-utils.cghub.ucsc.edu'

    availability_zone_re = re.compile( r'^([a-z]{2}-[a-z]+-[1-9][0-9]*)([a-z])$' )

    name_prefix_re = re.compile( r'^(/([0-9a-zA-Z.-][_0-9a-zA-Z.-]*))*' )
    name_re = re.compile( name_prefix_re.pattern + '/?$' )
    namespace_re = re.compile( name_prefix_re.pattern + '/$' )

    def __init__( self, availability_zone, namespace ):
        """
        Create an Context object.

        :param availability_zone: The availability zone to place EC2 resources like volumes and
        instances into. The AWS region to operate in is implied by this parameter since the
        region is a prefix of the availability zone string

        :param namespace: The prefix for names of EC2 resources. The namespace is string starting
        in '/' followed by zero or more components, separated by '/'. Components are non-empty
        strings consisting only of alphanumeric characters, '.', '-' or '_' and that don't start
        with '_'. The namespace argument will be encoded as ASCII. Unicode strings that can't be
        encoded as ASCII will be rejected.

        A note about our namespaces vs IAM's resource paths. IAM paths don't provide namespace
        isolation. In other words, it is not possible to have two users of the same name in two
        different paths. The by itself name has to be unique. For that reason, IAM resource paths
        are pretty much useless.

        >>> ctx = Context( 'us-west-1b', None )
        Traceback (most recent call last):
        ....
        ValueError: Need namespace

        >>> Context('us-west-1b', namespace='/').namespace
        '/'

        >>> Context('us-west-1b', namespace='/foo/').namespace
        '/foo/'

        >>> Context('us-west-1b', namespace='/foo/bar/').namespace
        '/foo/bar/'

        >>> Context('us-west-1b', namespace='')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace ''

        >>> Context('us-west-1b', namespace='foo')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace 'foo'

        >>> Context('us-west-1b', namespace='/foo')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace '/foo'

        >>> Context('us-west-1b', namespace='//foo/')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace '//foo/'

        >>> Context('us-west-1b', namespace='/foo//')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace '/foo//'

        >>> Context('us-west-1b', namespace='han//nes')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace 'han//nes'

        >>> Context('us-west-1b', namespace='/_foo/')
        Traceback (most recent call last):
        ....
        ValueError: Invalid namespace '/_foo/'

        >>> Context('us-west-1b', namespace=u'/foo/').namespace
        '/foo/'

        >>> Context('us-west-1b', namespace=u'/föo/').namespace
        Traceback (most recent call last):
        ....
        ValueError: 'ascii' codec can't encode characters in position 2-3: ordinal not in range(128)

        >>> import string
        >>> component = string.ascii_letters + string.digits + '-_.'
        >>> namespace = '/' + component + '/'
        >>> Context('us-west-1b', namespace=namespace).namespace == namespace
        True
        """
        super( Context, self ).__init__( )

        self.__iam = None
        self.__ec2 = None
        self.__s3 = None
        self.__sns = None
        self.__sqs = None

        self.availability_zone = availability_zone
        m = self.availability_zone_re.match( availability_zone )
        if not m:
            raise ValueError( "Can't extract region from availability zone '%s'"
                              % availability_zone )
        self.region = m.group( 1 )

        if namespace is None:
            raise ValueError( 'Need namespace' )
        try:
            namespace = namespace.encode( 'ascii' )
        except UnicodeEncodeError as e:
            raise ValueError( e )

        namespace = self.resolve_me( namespace )

        if not re.match( self.namespace_re, namespace ):
            raise ValueError( "Invalid namespace '%s'" % namespace )

        self.namespace = namespace

    @property
    def iam( self ):
        """
        :rtype: IAMConnection
        """
        if self.__iam is None:
            self.__iam = self.__aws_connect( iam, 'universal' )
        return self.__iam

    @property
    def ec2( self ):
        """
        :rtype: EC2Connection
        """
        if self.__ec2 is None:
            self.__ec2 = self.__aws_connect( ec2 )
        return self.__ec2

    @property
    def s3( self ):
        """
        :rtype: S3Connection
        """
        if self.__s3 is None:
            self.__s3 = self.__aws_connect( s3 )
        return self.__s3

    @property
    def sns( self ):
        """
        :rtype: SNSConnection
        """
        if self.__sns is None:
            self.__sns = self.__aws_connect( sns )
        return self.__sns

    @property
    def sqs( self ):
        """
        :rtype: SQSConnection
        """
        if self.__sqs is None:
            self.__sqs = self.__aws_connect( sqs )
        return self.__sqs

    def __aws_connect( self, aws_module, region=None ):
        conn = aws_module.connect_to_region( self.region if region is None else region )
        if conn is None:
            raise RuntimeError( "%s couldn't connect to region %s" % (
                aws_module.__name__, self.region ) )
        return conn

    def __enter__( self ):
        return self

    def __exit__( self, exc_type, exc_val, exc_tb ):
        self.close( )

    def close( self ):
        if self.__ec2 is not None: self.__ec2.close( )
        if self.__s3 is not None: self.__s3.close( )
        if self.__iam is not None: self.__iam.close( )
        if self.__sns is not None: self.__sns.close( )
        if self.__sqs is not None: self.__sqs.close( )

    @staticmethod
    def is_absolute_name( name ):
        """
        Returns True if the given name starts with a namespace.
        """
        return name[ 0:1 ] == '/'

    def absolute_name( self, name ):
        """
        Returns the absolute form of the specified resource name. If the specified name is
        already absolute, that name will be returned unchanged, otherwise the given name will be
        prefixed with the namespace this object was configured with.

        Relative names starting with underscores are disallowed.

        >>> ctx = Context( 'us-west-1b', namespace='/' )
        >>> ctx.absolute_name('bar')
        '/bar'
        >>> ctx.absolute_name('/bar')
        '/bar'
        >>> ctx.absolute_name('')
        '/'
        >>> ctx.absolute_name('/')
        '/'
        >>> ctx.absolute_name('_bar')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/_bar'
        >>> ctx.absolute_name('/_bar')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/_bar'

        >>> ctx = Context( 'us-west-1b', namespace='/foo/' )
        >>> ctx.absolute_name('bar')
        '/foo/bar'
        >>> ctx.absolute_name('bar/')
        '/foo/bar/'
        >>> ctx.absolute_name('/bar')
        '/bar'
        >>> ctx.absolute_name('')
        '/foo/'
        >>> ctx.absolute_name('/')
        '/'
        >>> ctx.absolute_name('_bar')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/foo/_bar'
        >>> ctx.absolute_name('/_bar')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/_bar'
        """
        if self.is_absolute_name( name ):
            result = name
        else:
            result = self.namespace + name
        if not self.name_re.match( result ):
            raise ValueError( "Invalid path '%s'" % result )
        return result

    def to_aws_name( self, name ):
        """
        Returns a transliteration of the name that safe to use for resource names on AWS. If the
        given name is relative, it converted to its absolute form before the transliteration.

        The transliteration uses two consequitive '_' to encode a single '_' and a single '_' to
        separate the name components. AWS-safe names are by definition absolute such that the
        leading separator can be removed. This leads to fairly readable AWS-safe names,
        especially for names in the root namespace, where the transliteration is the identity
        function if the input does not contain any '_'.

        This scheme only works if name components don't start with '_'. Without that condition,
        '/_' would become '___' the inverse of which is '_/'.

        >>> ctx = Context( 'us-west-1b', namespace='/' )

        >>> ctx.to_aws_name( 'foo' )
        'foo'
        >>> ctx.from_aws_name( 'foo' )
        'foo'

        Illegal paths that would introduce ambiguity need to raise an exception
        >>> ctx.to_aws_name('/_')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/_'
        >>> ctx.to_aws_name('/_/')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/_/'
        >>> ctx.from_aws_name('___')
        Traceback (most recent call last):
        ....
        ValueError: Invalid path '/_/'

        >>> ctx.to_aws_name( 'foo_bar')
        'foo__bar'
        >>> ctx.from_aws_name( 'foo__bar')
        'foo_bar'

        >>> ctx.to_aws_name( '/sub_ns/foo_bar')
        'sub__ns_foo__bar'
        >>> ctx.to_aws_name( 'sub_ns/foo_bar')
        'sub__ns_foo__bar'
        >>> ctx.from_aws_name( 'sub__ns_foo__bar' )
        'sub_ns/foo_bar'

        >>> ctx.to_aws_name( 'g_/' )
        'g___'
        >>> ctx.from_aws_name( 'g___' )
        'g_/'

        >>> ctx = Context( 'us-west-1b', namespace='/this_ns/' )

        >>> ctx.to_aws_name( 'foo' )
        'this__ns_foo'
        >>> ctx.from_aws_name( 'this__ns_foo' )
        'foo'

        >>> ctx.to_aws_name( 'foo_bar')
        'this__ns_foo__bar'
        >>> ctx.from_aws_name( 'this__ns_foo__bar')
        'foo_bar'

        >>> ctx.to_aws_name( '/other_ns/foo_bar' )
        'other__ns_foo__bar'
        >>> ctx.from_aws_name( 'other__ns_foo__bar' )
        '/other_ns/foo_bar'

        >>> ctx.to_aws_name( '/this_ns/foo_bar' )
        'this__ns_foo__bar'
        >>> ctx.from_aws_name( 'this__ns_foo__bar' )
        'foo_bar'
        """
        name = self.absolute_name( name )
        assert name.startswith( '/' )
        return name[ 1: ].replace( '_', '__' ).replace( '/', '_' )

    def from_aws_name( self, name ):
        """
        The inverse of to_aws_name(), except that the namespace is stripped from the input if it
        is relative to this context's name space.
        """
        name = '_'.join( s.replace( '_', '/' ) for s in name.split( '__' ) )
        name = '/' + name
        if not self.name_re.match( name ):
            raise ValueError( "Invalid path '%s'" % name )
        if name.startswith( self.namespace ):
            name = name[ len( self.namespace ): ]
        return name

    @staticmethod
    def ssh_pubkey_s3_key( fingerprint ):
        return 'ssh_pubkey:%s' % fingerprint

    def upload_ssh_pubkey( self, ssh_pubkey, fingerprint ):
        bucket = self.s3.lookup( self.s3_bucket_name )
        if bucket is None:
            bucket = self.s3.create_bucket( self.s3_bucket_name,
                                            location=self.region )
        s3_entry = S3Key( bucket )
        s3_entry.key = self.ssh_pubkey_s3_key( fingerprint )
        s3_entry.set_contents_from_string( ssh_pubkey )

    def register_ssh_pubkey( self, ec2_keypair_name, ssh_pubkey, force=False ):
        """
        Import the given OpenSSH public key  as a 'key pair' into EC2.

        There is no way to get to the actual public key once it has been imported to EC2.
        Openstack lets you do that and I don't see why Amazon decided to omit this functionality.
        To work around this, we store the public key in S3, identified by the public key's
        fingerprint. As long as we always check the fingerprint of the downloaded public SSH key
        against that of the EC2 keypair key, this method is resilient against malicious
        modifications of the keys stored in S3.

        :param ec2_keypair_name: the desired name of the EC2 key pair

        :param ssh_pubkey: the SSH public key in OpenSSH's native format, i.e. format that is used in ~/
        .ssh/authorized_keys

        :param force: overwrite existing EC2 keypair of the given name
        """
        fingerprint = ec2_keypair_fingerprint( ssh_pubkey, reject_private_keys=True )
        ec2_keypair = self.ec2.get_key_pair( ec2_keypair_name )
        if ec2_keypair is not None:
            if ec2_keypair.name != ec2_keypair_name:
                raise AssertionError( "Key pair names don't match." )
            if ec2_keypair.fingerprint != fingerprint:
                if force:
                    self.ec2.delete_key_pair( ec2_keypair_name )
                    ec2_keypair = None
                else:
                    raise UserError(
                        "Key pair %s already exists in EC2, but its fingerprint %s is "
                        "different from the fingerprint %s of the key to be imported. Use "
                        "the force option to overwrite the existing key pair." %
                        ( ec2_keypair.name, ec2_keypair.fingerprint, fingerprint ) )

        if ec2_keypair is None:
            ec2_keypair = self.ec2.import_key_pair( ec2_keypair_name, ssh_pubkey )
        assert ec2_keypair.fingerprint == fingerprint

        self.upload_ssh_pubkey( ssh_pubkey, fingerprint )
        self.publish_agent_message( Message( type=Message.TYPE_UPDATE_SSH_KEYS ) )
        return ec2_keypair

    def expand_keypair_globs( self, globs ):
        """
        Returns a list of EC2 key pair objects matching the specified globs. The order of the
        objects in the returned list will be consistent with the order of the globs and it will
        not contain any elements more than once. In other words, the returned list will start
        with all key pairs matching the first glob, followed by key pairs matching the second
        glob but not the first glob and so on.

        :rtype: list of Keypair
        """
        result = [ ]
        keypairs = dict( (keypair.name, keypair) for keypair in self.ec2.get_all_key_pairs( ) )
        for glob in globs:
            i = len( result )
            for name, keypair in keypairs.iteritems( ):
                if fnmatch.fnmatch( name, glob ):
                    result.append( keypair )

            # since we can't modify the set during iteration
            for keypair in result[ i: ]:
                keypairs.pop( keypair.name )
        return result

    def download_ssh_pubkey( self, ec2_keypair ):
        try:
            bucket = self.s3.get_bucket( self.s3_bucket_name )
            s3_entry = S3Key( bucket )
            s3_entry.key = self.ssh_pubkey_s3_key( ec2_keypair.fingerprint )
            ssh_pubkey = s3_entry.get_contents_as_string( )
        except S3ResponseError as e:
            if e.status == 404:
                raise UserError(
                    "There is no matching SSH pub key stored in S3 for EC2 key pair %s. Has "
                    "it been registered, e.g using the cgcloud's register-key command?" %
                    ec2_keypair.name )
            else:
                raise
        fingerprint_len = len( ec2_keypair.fingerprint.split( ':' ) )
        if fingerprint_len == 20: # 160 bit SHA-1
            # The fingerprint is that of a private key. We can't get at the private key so we
            # can't verify the public key either. So this is inherently insecure. However,
            # remember that the only reason why we are dealing with n EC2-generated private
            # key is that the Jenkins' EC2 plugin expects a 20 byte fingerprint. See
            # https://issues.jenkins-ci.org/browse/JENKINS-20142 for details. Once that issue
            # is fixed, we can switch back to just using imported keys and 16-byte fingerprints.
            pass
        elif fingerprint_len == 16: # 128 bit MD5
            fingerprint = ec2_keypair_fingerprint( ssh_pubkey )
            if ec2_keypair.fingerprint != fingerprint:
                raise UserError(
                    "Fingerprint mismatch for key %s! Expected %s but got %s. The EC2 keypair "
                    "doesn't match the public key stored in S3." %
                    ( ec2_keypair.name, ec2_keypair.fingerprint, fingerprint ) )
        return ssh_pubkey

    @property
    @memoize
    def iam_user_name( self ):
        try:
            return self.iam.get_user( )[
                'get_user_response' ][ 'get_user_result' ][ 'user' ][ 'user_name' ]
        except:
            return None

    current_user_placeholder = '__me__'

    def resolve_me( self, s ):
        placeholder = self.current_user_placeholder
        # avoid invoking self.iam_user_name unnecessarily
        return s.replace( placeholder, self.iam_user_name ) if placeholder in s  else s

    iam_agent_policies = {
        'ec2_read_only': {
            "Version": "2012-10-17",
            "Statement": [
                { "Effect": "Allow", "Resource": "*", "Action": "ec2:Describe*" },
                { "Effect": "Allow", "Resource": "*", "Action": "autoscaling:Describe*" },
                { "Effect": "Allow", "Resource": "*", "Action": "elasticloadbalancing:Describe*" },
                { "Effect": "Allow", "Resource": "*", "Action": [
                    "cloudwatch:ListMetrics",
                    "cloudwatch:GetMetricStatistics",
                    "cloudwatch:Describe*" ] } ] },
        's3_read_only': {
            "Version": "2012-10-17",
            "Statement": [
                { "Effect": "Allow", "Resource": "*", "Action": [ "s3:Get*", "s3:List*" ] } ] },
        'iam_read_only': {
            "Version": "2012-10-17",
            "Statement": [
                { "Effect": "Allow", "Resource": "*", "Action": [ "iam:List*", "iam:Get*" ] } ] },
        'sqs_custom': {
            "Version": "2012-10-17",
            "Statement": [
                { "Effect": "Allow", "Resource": "*", "Action": [
                    "sqs:Get*",
                    "sqs:List*",
                    "sqs:CreateQueue",
                    "sqs:SetQueueAttributes",
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessageBatch" ] } ] },
        'sns_custom': {
            "Version": "2012-10-17",
            "Statement": [
                { "Effect": "Allow", "Resource": "*", "Action": [
                    "sns:Get*",
                    "sns:List*",
                    "sns:CreateTopic",
                    "sns:Subscribe" ] } ] }
    }
    """
    The policies belonging to the role assumed by EC2 instances. This effectively controls
    what code running on our EC2 instances can do on AWS.
    """

    def setup_iam_ec2_role( self ):
        role_name = self.to_aws_name( 'agent' )
        policies = self.iam_agent_policies

        # Create role if necessary
        try:
            self.iam.create_role( role_name=role_name,
                                  assume_role_policy_document=json.dumps( {
                                      "Version": "2012-10-17",
                                      "Statement": [ {
                                          "Effect": "Allow",
                                          "Principal": { "Service": [ "ec2.amazonaws.com" ] },
                                          "Action": [ "sts:AssumeRole" ] }
                                      ] } ) )
        except BotoServerError as e:
            if e.status == 409 and e.error_code == 'EntityAlreadyExists':
                pass
            else:
                raise

        # Delete superfluous policies
        policy_names = set( self.iam.list_role_policies( role_name=role_name )[
            'list_role_policies_response' ][ 'list_role_policies_result' ][ 'policy_names' ] )
        for policy_name in policy_names.difference( set( policies.keys( ) ) ):
            self.iam.delete_role_policy( role_name=role_name,
                                         policy_name=policy_name )

        # Create expected policies
        for policy_name, policy in policies.iteritems( ):
            current_policy = None
            try:
                current_policy = json.loads( urllib.unquote(
                    self.iam.get_role_policy( role_name=role_name,
                                              policy_name=policy_name )[
                        'get_role_policy_response' ][
                        'get_role_policy_result' ][
                        'policy_document' ] ) )
            except BotoServerError as e:
                if e.status == 404 and e.error_code == 'NoSuchEntity':
                    pass
                else:
                    raise
            if current_policy != policy:
                self.iam.put_role_policy( role_name=role_name,
                                          policy_name=policy_name,
                                          policy_document=json.dumps( policy ) )

        return role_name

    _agent_topic_name = "cghub-cloud-agent-notifications"

    @property
    def agent_queue_name( self ):
        host_qualifier = socket.gethostname( ).replace( '.', '-' )
        return self._agent_topic_name + '/' + host_qualifier

    @property
    @memoize
    def agent_topic_arn( self ):
        """
        The ARN of the SNS topic on which the agents listen for messages and returns its ARN.
        """
        # Note that CreateTopic is idempotent
        return self.sns.create_topic( self._agent_topic_name )[
            'CreateTopicResponse' ][ 'CreateTopicResult' ][ 'TopicArn' ]

    def publish_agent_message( self, message ):
        """
        :type message: Message
        """
        self.sns.publish( self.agent_topic_arn, message.to_sns( ) )
